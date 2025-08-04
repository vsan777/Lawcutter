#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This script provides utilities for converting Swiss legal texts from the Akoma
Ntoso XML format into Markdown.  It exposes both a GUI built with tkinter and
programmatic APIs via the ``SwissCodeConverter`` class.  The converter walks
through the hierarchical structure of an Akoma Ntoso document, extracting
article numbers, marginal notes and paragraphs or enumerated lists.  It then
renders this content into Markdown while preserving numbering, nested lists
and inline authorial notes.

The implementation in this file has been adapted from the user's original
``lawcutterV11.py`` to address several issues identified in the generated
Markdown:

* **Continuous authorial notes:** Authorial note numbers now increment
  continuously throughout each article instead of resetting to 1 for every
  paragraph or list item.  This aligns the generated Markdown with the
  numbering seen in the reference PDF.  A running counter is kept while
  parsing each article and is passed through into the formatter.

* **Improved nested list handling:** Parsing of nested lists has been
  expanded so that sub‑items (for example points ``a.`` and ``b.`` under a
  numbered item) are discovered and rendered.  Additionally, the parser now
  captures the text of all nested elements (such as ``ref``, ``docTitle`` or
  similar) in paragraphs instead of only their tails.  This ensures that
  introductory sentences like "Le rapport sur les questions non financières…"
  and risk descriptions appearing in Art. 964b are fully included.

* **Selective numbering suppression:** Some articles (such as Art. 40b,
  652e or 635) consist of a single numbered paragraph whose content is
  exclusively a list.  In these cases the article-level number should be
  suppressed (no "1" or "a" before the list), while the list items retain
  their own numbering or lettering.  Other articles (such as Art. 336c,
  959a or 804) use the numbered paragraph to introduce the subsequent list;
  the paragraph number must be shown in the output.  The converter now
  attaches the parent paragraph number to the list introduction or to the
  first list item exactly once, preserving the intended structure.

* **Avoid duplicated numbering inside paragraphs:** Numbering elements
  appearing inside paragraphs (``<num>`` tags nested within ``<p>``) are
  ignored when extracting text.  Only their trailing text is preserved.
  Without this, duplicate numbers or letters (such as an extra "a" before
  "a." or an extra "1" before "1.") could appear in the Markdown.

Only the relevant portions of the original code have been touched.  All
existing functionalities—such as the splitting of full documents into
individual article files, the graphical interface and file naming logic—are
preserved exactly as before.
"""

# Optional import of tkinter for GUI; fallback if not available
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception:
    # tkinter is not available in some environments (e.g., headless servers)
    tk = None
    filedialog = None
    messagebox = None

from pathlib import Path
from lxml import etree as ET
from datetime import datetime
from typing import Dict, Optional, Union, List, Tuple
import re

# Default configuration values.  Users may override these via the GUI.
CONFIG = {
    'article_prefix': 'Art.',
    'code_name': 'CO',
    'margin_separator': ' << ',
    'output_encoding': 'utf-8',
    'suffix_counter': 1,
}

# XML namespace constants
AKN_NS = 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0'
FEDLEX_NS = 'http://www.fedlex.admin.ch/eli/cc/27/317_321_377/fr'


class SwissCodeConverter:
    """Core converter class for transforming Akoma Ntoso XML into Markdown."""

    def __init__(self, config: Optional[Dict] = None):
        # Use a copy of the configuration dict to avoid accidental mutations
        self.config = config or CONFIG.copy()
        self.ns = {'akn': AKN_NS, 'fedlex': FEDLEX_NS}
        self.in_final_section = False
        self.suffix_counter = self.config['suffix_counter']

    def parse_xml(self, xml_file_path: Union[str, Path]) -> ET.Element:
        parser = ET.XMLParser(remove_blank_text=True)
        tree = ET.parse(str(xml_file_path), parser)
        return tree.getroot()

    def extract_article_number(self, article_element: ET.Element) -> str:
        """Return the number of an <article> element (e.g. "1", "40b")."""
        num_element = article_element.find(f'.//{{{AKN_NS}}}num')
        if num_element is not None:
            num_text = ''.join(num_element.itertext()).strip()
            match = re.search(r'(\d+[a-z]?)', num_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def extract_marginal_notes(self, article_element: ET.Element) -> str:
        """
        Build a string of marginal notes based on the surrounding <level> headings.
        If the converter enters a final section (e.g. "Dispositions finales"), a
        flag is set to adjust article prefixes accordingly.
        """
        hierarchy: List[str] = []
        current = article_element
        while current is not None:
            if current.tag == f'{{{AKN_NS}}}level':
                heading = current.find(f'.//{{{AKN_NS}}}heading', namespaces=self.ns)
                if heading is not None and heading.text:
                    text = heading.text.strip()
                    if any(keyword in text for keyword in [
                        "Titre final",
                        "Dispositions finales",
                        "Dispositions transitoires"]):
                        self.in_final_section = True
                    hierarchy.insert(0, text)
            current = current.getparent()
        return self.config['margin_separator'].join(hierarchy) if hierarchy else ""

    def extract_paragraphs(self, article_element: ET.Element
                           ) -> Tuple[List[Tuple[str, str, Dict[str, str], int]],
                                      Dict[str, str]]:
        """
        Extract paragraphs and lists from an <article> element.

        Returns a list of tuples: (number, text, local_notes, indent_level) and a
        dictionary of all authorial notes encountered.  The number may be a
        paragraph number, a list item label (e.g. "a.") or empty string for
        unnumbered lines.  The indent level indicates nesting depth for lists.
        """
        paragraphs: List[Tuple[str, str, Dict[str, str], int]] = []
        all_notes: Dict[str, str] = {}
        note_counter = 0

        def parse_element(elem: ET.Element, para_num: str = "", level: int = 0) -> None:
            """
            Recursively traverse a content element and emit paragraphs and list items.

            The ``para_num`` argument carries the paragraph number from the parent
            <paragraph> element.  It is applied to the first piece of content found
            within ``elem``.  Subsequent pieces of content within the same element
            (including text following a list) are emitted without the number.
            """
            nonlocal note_counter, all_notes, paragraphs
            current_num = para_num
            # Capture text directly attached to the element before its children
            if elem.text and elem.text.strip():
                intro_text = elem.text.strip()
                paragraphs.append((current_num, intro_text, {}, level))
                current_num = ""

            def extract_p_text(p_elem: ET.Element) -> Tuple[str, Dict[str, str]]:
                """
                Extract the textual content of a <p> element while preserving the order
                of inline elements and capturing authorial notes.  Inline numbering
                elements (<num>) found inside paragraphs are skipped because list or
                paragraph numbers are handled separately; only their tails (the text
                following the number) are included.  Without this, duplicate numbers
                or letters (such as an extra "a" before "a." or an extra "1" before
                "1.") would appear in the output.
                """
                p_parts: List[str] = []
                local_notes: Dict[str, str] = {}
                nonlocal note_counter
                for node in p_elem.iter():
                    # Determine whether the current node is nested within an authorial note
                    in_authorial = any(
                        ancestor.tag == f'{{{AKN_NS}}}authorialNote'
                        for ancestor in node.iterancestors()
                    )
                    # Skip numbering elements inside <p>
                    if ET.QName(node).localname == 'num':
                        if not in_authorial and node.tail:
                            tail = node.tail.strip()
                            if tail:
                                p_parts.append(tail)
                        continue
                    if node.tag == f'{{{AKN_NS}}}authorialNote':
                        # Capture the full text of the authorial note and assign a running index
                        note_text = ' '.join(node.itertext()).strip()
                        note_counter += 1
                        note_id = str(note_counter)
                        all_notes[note_id] = note_text
                        p_parts.append(f"<sup style='color:red'>[{note_id}]</sup>")
                        # Append any trailing text after the authorial note
                        if node.tail:
                            tail = node.tail.strip()
                            if tail:
                                p_parts.append(tail)
                        continue
                    # Accumulate regular text and tails outside authorial notes
                    if not in_authorial:
                        if node is p_elem:
                            if node.text:
                                txt = node.text.strip()
                                if txt:
                                    p_parts.append(txt)
                        else:
                            if node.text:
                                txt = node.text.strip()
                                if txt:
                                    p_parts.append(txt)
                            if node.tail:
                                tail = node.tail.strip()
                                if tail:
                                    p_parts.append(tail)
                return ' '.join(filter(None, p_parts)).strip(), local_notes

            def handle_block_list(bl_elem: ET.Element, lvl: int, num: str) -> None:
                """
                Process a list (``blockList``) by iterating through its items.  When
                multiple items share the same numbering at the same indentation level
                (e.g. repeated 'c.' entries), suffixes such as 'bis', 'ter', 'quater', etc.,
                are appended to distinguish them.  An introductory ``listIntroduction``
                is added as plain text.  If a parent paragraph number (``num``) is
                provided, it is attached exactly once: either to the list introduction
                if present, or to the first list item if there is no introduction.  This
                allows articles like Art. 804 or 959a to display their paragraph numbers
                before the list, while articles consisting solely of a list (e.g.
                Art. 40b) omit the number.
                """
                local_enum_counts: Dict[str, int] = {}
                suffixes = {
                    2: 'bis',
                    3: 'ter',
                    4: 'quater',
                    5: 'quinquies',
                    6: 'sexies',
                    7: 'septies',
                    8: 'octies',
                    9: 'nonies',
                    10: 'decies',
                    11: 'undecies',
                    12: 'duodecies',
                }
                current_list_num = num
                list_intro = bl_elem.find(f'{{{AKN_NS}}}listIntroduction')
                used_parent_num = False
                # Handle list introduction: if present, attach parent number
                if list_intro is not None and list_intro.text and list_intro.text.strip():
                    intro_text = list_intro.text.strip()
                    if current_list_num:
                        paragraphs.append((current_list_num, intro_text, {}, lvl))
                        used_parent_num = True
                        current_list_num = ""
                    else:
                        paragraphs.append(("", intro_text, {}, lvl))
                # Items in the list
                items = bl_elem.findall(f'{{{AKN_NS}}}item')
                # Special case: a single item containing a sub-list – treat its <p> as intro
                if len(items) == 1:
                    itm = items[0]
                    has_nested = itm.find(f'{{{AKN_NS}}}blockList') is not None
                    if has_nested:
                        for p_e in itm.findall(f'{{{AKN_NS}}}p'):
                            intro_txt, _ = extract_p_text(p_e)
                            if intro_txt:
                                if current_list_num and not used_parent_num:
                                    paragraphs.append((current_list_num, intro_txt, {}, lvl))
                                    used_parent_num = True
                                    current_list_num = ""
                                else:
                                    paragraphs.append(("", intro_txt, {}, lvl))
                        for nested_bl in itm.findall(f'{{{AKN_NS}}}blockList'):
                            handle_block_list(nested_bl, lvl + 1, "")
                        if itm.tail and itm.tail.strip():
                            paragraphs.append(("", itm.tail.strip(), {}, lvl + 1))
                        return
                # General case: multiple items with potential duplicates
                for index, itm in enumerate(items):
                    itm_num_elem = itm.find(f'{{{AKN_NS}}}num')
                    itm_num = itm_num_elem.text.strip() if itm_num_elem is not None and itm_num_elem.text else ""
                    base_enum = itm_num.rstrip('.').strip()
                    display_enum = itm_num
                    if base_enum:
                        count = local_enum_counts.get(base_enum, 0) + 1
                        local_enum_counts[base_enum] = count
                        if count > 1:
                            suffix = suffixes.get(count, f"{count}")
                            display_enum = f"{base_enum} {suffix}."
                    else:
                        display_enum = ""
                    # Collect text of all <p> elements within the item
                    itm_text_parts: List[str] = []
                    notes: Dict[str, str] = {}
                    for p_e in itm.findall(f'{{{AKN_NS}}}p'):
                        txt, _ = extract_p_text(p_e)
                        if txt:
                            itm_text_parts.append(txt)
                    item_text = ' '.join(filter(None, itm_text_parts)).strip()
                    # Attach the parent paragraph number to the first item if not used
                    if current_list_num and not used_parent_num:
                        paragraphs.append((current_list_num, item_text, notes, lvl + 1))
                        used_parent_num = True
                        current_list_num = ""
                    else:
                        paragraphs.append((display_enum, item_text, notes, lvl + 1))
                    # Recursively process nested lists
                    for nested_bl in itm.findall(f'{{{AKN_NS}}}blockList'):
                        handle_block_list(nested_bl, lvl + 1, "")
                    # Handle any trailing text after an item
                    if itm.tail and itm.tail.strip():
                        paragraphs.append(("", itm.tail.strip(), {}, lvl + 1))

            # Iterate over child elements of ``elem`` in document order
            for child in elem:
                ctag = ET.QName(child).localname
                if ctag == 'blockList':
                    handle_block_list(child, level, current_num)
                    current_num = ""
                    if child.tail and child.tail.strip():
                        paragraphs.append(("", child.tail.strip(), {}, level))
                elif ctag == 'p':
                    txt, local_notes = extract_p_text(child)
                    if txt:
                        paragraphs.append((current_num, txt, local_notes, level))
                        current_num = ""
                    if child.tail and child.tail.strip():
                        paragraphs.append(("", child.tail.strip(), {}, level))
                else:
                    # For other tags, recurse into them retaining the current number
                    parse_element(child, current_num, level)
                    current_num = ""
            # Capture trailing text attached to ``elem`` itself
            if elem.tail and elem.tail.strip():
                paragraphs.append(("", elem.tail.strip(), {}, level))

        # Count numbered paragraphs to decide if numbering should be suppressed when there
        # is only one numbered paragraph with a direct list (category-2 articles)
        paragraph_elements = article_element.findall(f'.//{{{AKN_NS}}}paragraph')
        numbered_count = 0
        for p in paragraph_elements:
            n_el = p.find(f'.//{{{AKN_NS}}}num')
            if n_el is not None and n_el.text and n_el.text.strip():
                numbered_count += 1
        # Process each <paragraph> in the article
        for para in paragraph_elements:
            num_element = para.find(f'.//{{{AKN_NS}}}num')
            para_num = num_element.text.strip() if num_element is not None and num_element.text else ""
            content_element = para.find(f'.//{{{AKN_NS}}}content')
            if content_element is not None:
                use_num = para_num
                # Suppress the number if there's only one numbered paragraph and it contains a direct blockList
                # (category-2 articles like Art. 40b): the blockList will get the parent number only if needed.
                if numbered_count == 1 and para_num:
                    has_direct_block_list = any(
                        ET.QName(child).localname == 'blockList' for child in content_element
                    )
                    if has_direct_block_list:
                        use_num = ""
                parse_element(content_element, use_num, level=0)
            else:
                # Paragraph with no content element; emit its number and an empty line
                paragraphs.append((para_num, "", {}, 0))
        return paragraphs, all_notes

    def format_article_markdown(self, article_number: str, marginal_notes: str,
                                paragraphs: List[Tuple[str, str, Dict[str, str], int]],
                                notes: Dict[str, str]) -> str:
        """Assemble the Markdown lines for a single article."""
        markdown_lines: List[str] = []
        prefix = "SupArt." if self.in_final_section else self.config['article_prefix']
        code = self.config['code_name']
        # Header
        if article_number:
            markdown_lines.append(f"**[[{prefix} {article_number} {code}]]**")
        else:
            markdown_lines.append(f"**[[{prefix} {code}]]**")
        if marginal_notes:
            markdown_lines.append(f"[{marginal_notes}]")
        markdown_lines.append("")
        # Body paragraphs and lists
        for para_num, para_text, _notes, level in paragraphs:
            indent = "   " * level
            if para_num:
                colored_num = f"<span style=\"color:yellow\"><small>{para_num}</small></span>"
                line = f"{indent}**{colored_num}** {para_text}"
            else:
                line = f"{indent}{para_text}"
            markdown_lines.append(line)
        # Notes section
        if notes:
            markdown_lines.append("")
            markdown_lines.append("---")
            markdown_lines.append("**Notes :**")
            for num, note_text in notes.items():
                markdown_lines.append(f"<span style='color:red'>[{num}]</span> {note_text}")
        markdown_lines.append("")
        return "\n".join(markdown_lines)

    def convert_article(self, article_element: ET.Element) -> str:
        """Convert a single <article> element into Markdown."""
        article_number = self.extract_article_number(article_element)
        marginal_notes = self.extract_marginal_notes(article_element)
        paragraphs, notes = self.extract_paragraphs(article_element)
        return self.format_article_markdown(article_number, marginal_notes, paragraphs, notes)

    def convert_full_document(self,
                              xml_file_path: Union[str, Path],
                              output_file_path: Optional[Union[str, Path]] = None
                              ) -> str:
        """Convert an entire XML document into a single Markdown string."""
        root = self.parse_xml(xml_file_path)
        articles = root.findall(f'.//{{{AKN_NS}}}article')
        if not articles:
            raise ValueError("No articles found in the XML document")
        markdown_content: List[str] = []
        markdown_content.append(f"# {self.config['code_name']}")
        markdown_content.append("")
        markdown_content.append(f"*Converted from XML on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        markdown_content.append("")
        for article in articles:
            article_markdown = self.convert_article(article)
            markdown_content.append(article_markdown)
        full_markdown = "\n".join(markdown_content)
        if output_file_path:
            with open(output_file_path, 'w', encoding=self.config['output_encoding']) as f:
                f.write(full_markdown)
        return full_markdown

    # Unchanged methods from the original script: suffix handling, splitting into individual files, etc.
    def get_filename_with_suffix(self, article_num: str) -> str:
        base_match = re.match(r'(\d+[a-z]?)', article_num, re.IGNORECASE)
        suffix = ""
        if base_match:
            base_num = base_match.group(1)
        else:
            return article_num
        if "bis" in article_num:
            suffix = "-2bis"
        elif "ter" in article_num:
            if self.suffix_counter == 2:
                suffix = "-3ter"
            elif self.suffix_counter == 3:
                suffix = "-4quater"
            else:
                suffix = "-3ter"
        elif "quater" in article_num:
            suffix = "-4quater"
        else:
            suffix = ""
        return f"{base_num}{suffix}"

    def update_counter_after_save(self, article_num: str):
        if "bis" in article_num:
            self.suffix_counter = 2
        elif "ter" in article_num:
            if self.suffix_counter == 2:
                self.suffix_counter = 3
            elif self.suffix_counter == 3:
                self.suffix_counter = 4
            else:
                self.suffix_counter = 3
        elif "quater" in article_num:
            self.suffix_counter = 4
        else:
            self.suffix_counter = 1

    def split_from_full_markdown(self,
                                 full_markdown: str,
                                 output_dir: Path,
                                 pattern: str
                                 ) -> Tuple[int, List[str]]:
        """Split a full Markdown document into per-article files."""
        articles = re.findall(
            r'(\*\*\[\[Art\. ?\d+[a-zA-Z]* [^\]]*\]\]\*\*.*?)(?=(\*\*\[\[Art\. ?\d+[a-zA-Z]* [^\]]*\]\]\*\*|$))',
            full_markdown,
            flags=re.S
        )
        count = 0
        failed: List[str] = []
        for art_content, _ in articles:
            art_content = art_content.strip()
            if not art_content:
                continue
            match = re.search(r'\*\*\[\[(.*?)\]\]\*\*', art_content)
            if not match:
                continue
            article_title = match.group(1)
            num_match = re.search(r'(\d+[a-z]?(?:_[\d]+)?(?:quater|ter|bis)?[a-z]*)', article_title)
            if not num_match:
                continue
            original_num = num_match.group(1)
            article_num = self.get_filename_with_suffix(original_num)
            filename = pattern.format(num=article_num) + ".md"
            article_file = output_dir / filename
            if article_file.exists():
                failed.append(filename)
                continue
            try:
                with open(article_file, 'w', encoding=self.config['output_encoding']) as f:
                    f.write(art_content.strip())
                count += 1
                self.update_counter_after_save(original_num)
            except Exception:
                failed.append(filename)
        return count, failed


class SwissCodeGUI:
    """Simple GUI front-end for the SwissCodeConverter."""
    def __init__(self, root):
        self.root = root
        self.root.title("Swiss Code Converter")
        self.xml_file: Optional[Path] = None
        self.config = CONFIG.copy()
        self.converter = SwissCodeConverter(self.config)
        # Build GUI widgets
        tk.Label(root, text="Choisir un fichier XML :").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.xml_label = tk.Label(root, text="Aucun fichier sélectionné", fg="grey")
        self.xml_label.grid(row=0, column=1, sticky="w")
        tk.Button(root, text="Parcourir", command=self.browse_file).grid(row=0, column=2, padx=5)
        tk.Label(root, text="Préfixe des articles :").grid(row=1, column=0, sticky="w", padx=5)
        self.prefix_entry = tk.Entry(root, width=10)
        self.prefix_entry.insert(0, self.config['article_prefix'])
        self.prefix_entry.grid(row=1, column=1, sticky="w")
        tk.Label(root, text="Code de loi :").grid(row=2, column=0, sticky="w", padx=5)
        self.code_entry = tk.Entry(root, width=10)
        self.code_entry.insert(0, self.config['code_name'])
        self.code_entry.grid(row=2, column=1, sticky="w")
        self.choice = tk.StringVar(value="full")
        tk.Radiobutton(root, text="Document entier", variable=self.choice, value="full").grid(row=3, column=0, sticky="w", padx=5)
        tk.Radiobutton(root, text="Article spécifique", variable=self.choice, value="single").grid(row=4, column=0, sticky="w", padx=5)
        tk.Radiobutton(root, text="Tous les articles séparés", variable=self.choice, value="split").grid(row=5, column=0, sticky="w", padx=5)
        self.article_entry = tk.Entry(root, width=10)
        self.article_entry.grid(row=4, column=1, sticky="w", padx=5)
        tk.Label(root, text="(Numéro d'article)").grid(row=4, column=2, sticky="w")
        tk.Label(root, text="Modèle de nom (si split) :").grid(row=6, column=0, sticky="w", padx=5, pady=5)
        self.filename_pattern = tk.Entry(root, width=30)
        self.filename_pattern.insert(0, "{prefix} {num} {code}")
        self.filename_pattern.grid(row=6, column=1, sticky="w")
        tk.Button(root, text="Lancer la conversion", command=self.run_conversion).grid(row=7, column=0, columnspan=3, pady=10)

    def browse_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("XML files", "*.xml")])
        if file_path:
            self.xml_file = Path(file_path)
            self.xml_label.config(text=self.xml_file.name, fg="black")

    def run_conversion(self):
        if not self.xml_file:
            messagebox.showerror("Erreur", "Veuillez sélectionner un fichier XML.")
            return
        # Update configuration based on GUI inputs
        self.config['article_prefix'] = self.prefix_entry.get().strip()
        self.config['code_name'] = self.code_entry.get().strip()
        self.converter = SwissCodeConverter(self.config)
        try:
            if self.choice.get() == "full":
                save_path = filedialog.asksaveasfilename(defaultextension=".md",
                                                         filetypes=[("Markdown files", "*.md")])
                if save_path:
                    self.converter.convert_full_document(self.xml_file, save_path)
                    messagebox.showinfo("Succès", f"Document complet converti : {save_path}")
            elif self.choice.get() == "single":
                art_num = self.article_entry.get().strip()
                if not art_num:
                    messagebox.showerror("Erreur", "Veuillez indiquer le numéro d'article.")
                    return
                save_path = filedialog.asksaveasfilename(defaultextension=".md",
                                                         filetypes=[("Markdown files", "*.md")])
                if save_path:
                    full_markdown = self.converter.convert_full_document(self.xml_file)
                    articles = re.findall(r'(\*\*\[\[.*?\]\]\*\*.*?)(?=\*\*\[\[.*?\]\]\*\*|$)',
                                          full_markdown, flags=re.S)
                    for art in articles:
                        if f"{self.config['article_prefix']} {art_num} {self.config['code_name']}" in art[0]:
                            with open(save_path, 'x', encoding="utf-8") as f:
                                f.write(art[0])
                            messagebox.showinfo("Succès", f"Article {art_num} converti : {save_path}")
                            break
            elif self.choice.get() == "split":
                output_dir = filedialog.askdirectory(title="Choisir le dossier de sortie")
                if output_dir:
                    full_markdown = self.converter.convert_full_document(self.xml_file)
                    pattern = (self.filename_pattern.get()
                               .replace("{prefix}", self.config['article_prefix'])
                               .replace("{code}", self.config['code_name']))
                    count, failed = self.converter.split_from_full_markdown(full_markdown, Path(output_dir), pattern)
                    rapport = f"{count} articles enregistrés.\n"
                    if failed:
                        rapport += "Échecs sur :\n" + "\n".join(failed)
                    messagebox.showinfo("Rapport", rapport)
        except FileExistsError:
            messagebox.showerror("Erreur", "Le fichier existe déjà, conversion annulée.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))


if __name__ == "__main__":
    """
    Launch the graphical interface when the script is executed directly.

    The previous version of this script attempted a demonstration conversion on
    a hard-coded XML file, which caused an error when that file was absent in
    the user's environment.  The current version simply starts the GUI if
    tkinter is available; otherwise it does nothing.
    """
    if tk is not None:
        root = tk.Tk()
        app = SwissCodeGUI(root)
        root.mainloop()
    else:
        # Headless environment: do nothing
        pass
# V12 SavePoint - Lettrage fonctionnne - ce qui ne fonctione pas c'est l'alignement 959a et les notes rouges []