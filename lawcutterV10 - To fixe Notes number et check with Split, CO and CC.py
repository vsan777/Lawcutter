#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from lxml import etree as ET
from datetime import datetime
from typing import Dict, Optional, Union, List, Tuple
import re

CONFIG = {
    'article_prefix': 'Art.',
    'code_name': 'CO',
    'margin_separator': ' << ',
    'output_encoding': 'utf-8',
    'suffix_counter': 1,
}

AKN_NS = 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0'
FEDLEX_NS = 'http://www.fedlex.admin.ch/eli/cc/27/317_321_377/fr'

class SwissCodeConverter:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or CONFIG.copy()
        self.ns = {'akn': AKN_NS, 'fedlex': FEDLEX_NS}
        self.in_final_section = False
        self.suffix_counter = self.config['suffix_counter']

    def parse_xml(self, xml_file_path: Union[str, Path]) -> ET.Element:
        parser = ET.XMLParser(remove_blank_text=True)
        tree = ET.parse(str(xml_file_path), parser)
        return tree.getroot()

    def extract_article_number(self, article_element: ET.Element) -> str:
        num_element = article_element.find(f'.//{{{AKN_NS}}}num')
        if num_element is not None:
            num_text = ''.join(num_element.itertext()).strip()
            match = re.search(r'(\d+[a-z]?)', num_text)
            if match:
                return match.group(1)
        return ""

    def extract_marginal_notes(self, article_element: ET.Element) -> str:
        hierarchy = []
        current = article_element
        while current is not None:
            if current.tag == f'{{{AKN_NS}}}level':
                heading = current.find(f'.//{{{AKN_NS}}}heading', namespaces=self.ns)
                if heading is not None and heading.text:
                    text = heading.text.strip()
                    if any(keyword in text for keyword in ["Titre final", "Dispositions finales", "Dispositions transitoires"]):
                        self.in_final_section = True
                    hierarchy.insert(0, text)
            current = current.getparent()
        return self.config['margin_separator'].join(hierarchy) if hierarchy else ""

    def extract_paragraphs(self, article_element: ET.Element) -> List[Tuple[str, str, Dict[str, str], int]]:
        """Extrait paragraphes et listes numérotées avec gestion des notes d’auteur."""
        paragraphs = []

        def parse_content(content_element, para_num="", level=0):
            # Cas 1 : content contient un blockList (listIntroduction + items)
            blocklist = content_element.find(f'{{{AKN_NS}}}blockList')
            if blocklist is not None:
                # Ajoute l'intro de la liste s'il y a
                list_intro = blocklist.find(f'{{{AKN_NS}}}listIntroduction')
                if list_intro is not None and list_intro.text:
                    intro_text = list_intro.text.strip()
                    if intro_text:
                        paragraphs.append((para_num, intro_text, {}, level))
                # Ajoute chaque item de la liste
                for item in blocklist.findall(f'{{{AKN_NS}}}item'):
                    item_num_elem = item.find(f'{{{AKN_NS}}}num')
                    item_num = item_num_elem.text.strip() if item_num_elem is not None and item_num_elem.text else ""
                    item_p = item.find(f'{{{AKN_NS}}}p')
                    notes = {}
                    item_text = ""
                    if item_p is not None:
                        for node in item_p.iter():
                            in_authorial = any(
                                ancestor.tag == f'{{{AKN_NS}}}authorialNote'
                                for ancestor in node.iterancestors()
                            )
                            if node.tag == f'{{{AKN_NS}}}authorialNote':
                                note_num = node.get("num", str(len(notes) + 1))
                                note_text = ' '.join(node.itertext()).strip()
                                notes[note_num] = note_text
                                item_text += f"<sup style='color:red'>[{note_num}]</sup>"
                                if node.tail:
                                    item_text += node.tail.strip() + " "
                            elif node is item_p:
                                if node.text and not in_authorial:
                                    item_text += node.text.strip() + " "
                            else:
                                if node.tail and not in_authorial:
                                    item_text += node.tail.strip() + " "
                    paragraphs.append((item_num, item_text.strip(), notes, level + 1))
            else:
                # Cas 2 : paragraphes simples (ou <content> direct)
                for p in content_element.findall(f'./{{{AKN_NS}}}p'):
                    notes = {}
                    para_text = ""
                    for node in p.iter():
                        in_authorial = any(
                            ancestor.tag == f'{{{AKN_NS}}}authorialNote'
                            for ancestor in node.iterancestors()
                        )
                        if node.tag == f'{{{AKN_NS}}}authorialNote':
                            note_num = node.get("num", str(len(notes) + 1))
                            note_text = ' '.join(node.itertext()).strip()
                            notes[note_num] = note_text
                            para_text += f"<sup style='color:red'>[{note_num}]</sup>"
                            if node.tail:
                                para_text += node.tail.strip() + " "
                        elif node is p:
                            if node.text and not in_authorial:
                                para_text += node.text.strip() + " "
                        else:
                            if node.tail and not in_authorial:
                                para_text += node.tail.strip() + " "
                    if para_text.strip():
                        paragraphs.append((para_num, para_text.strip(), notes, level))

        for para in article_element.findall(f'.//{{{AKN_NS}}}paragraph'):
            num_element = para.find(f'.//{{{AKN_NS}}}num')
            para_num = num_element.text.strip() if num_element is not None and num_element.text else ""
            content_element = para.find(f'.//{{{AKN_NS}}}content')
            if content_element is not None:
                parse_content(content_element, para_num, level=0)

        return paragraphs

    def format_article_markdown(self, article_number: str, marginal_notes: str,
                                paragraphs: List[Tuple[str, str, Dict[str, str], int]]) -> str:
        markdown_lines: List[str] = []
        all_notes: Dict[str, str] = {}
        prefix = "SupArt." if self.in_final_section else self.config['article_prefix']
        code = self.config['code_name']

        if article_number:
            markdown_lines.append(f"**[[{prefix} {article_number} {code}]]**")
        else:
            markdown_lines.append(f"**[[{prefix} {code}]]**")

        if marginal_notes:
            markdown_lines.append(f"[{marginal_notes}]")
        markdown_lines.append("")

        for para_num, para_text, notes, level in paragraphs:
            if para_num:
                colored_num = f'<span style="color:yellow"><small>{para_num}</small></span>'
                indent = "   " * level
                line = f"{indent}**{colored_num}** {para_text}"
            else:
                indent = "   " * level
                line = f"{indent}{para_text}"
            markdown_lines.append(line)
            all_notes.update(notes)

        if all_notes:
            markdown_lines.append("")
            markdown_lines.append("---")
            markdown_lines.append("**Notes :**")
            for num, note_text in all_notes.items():
                markdown_lines.append(f"<span style='color:red'>[{num}]</span> {note_text}")

        markdown_lines.append("")
        return "\n".join(markdown_lines)

    def convert_article(self, article_element: ET.Element) -> str:
        article_number = self.extract_article_number(article_element)
        marginal_notes = self.extract_marginal_notes(article_element)
        paragraphs = self.extract_paragraphs(article_element)
        return self.format_article_markdown(article_number, marginal_notes, paragraphs)

    def convert_full_document(self, xml_file_path: Union[str, Path], output_file_path: Optional[Union[str, Path]] = None) -> str:
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

    def split_from_full_markdown(self, full_markdown: str, output_dir: Path, pattern: str) -> Tuple[int, List[str]]:
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
    def __init__(self, root):
        self.root = root
        self.root.title("Swiss Code Converter")
        self.xml_file: Optional[Path] = None
        self.config = CONFIG.copy()
        self.converter = SwissCodeConverter(self.config)

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

        self.config['article_prefix'] = self.prefix_entry.get().strip()
        self.config['code_name'] = self.code_entry.get().strip()
        self.converter = SwissCodeConverter(self.config)

        try:
            if self.choice.get() == "full":
                save_path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown files", "*.md")])
                if save_path:
                    self.converter.convert_full_document(self.xml_file, save_path)
                    messagebox.showinfo("Succès", f"Document complet converti : {save_path}")
            elif self.choice.get() == "single":
                art_num = self.article_entry.get().strip()
                if not art_num:
                    messagebox.showerror("Erreur", "Veuillez indiquer le numéro d'article.")
                    return
                save_path = filedialog.asksaveasfilename(defaultextension=".md", filetypes=[("Markdown files", "*.md")])
                if save_path:
                    full_markdown = self.converter.convert_full_document(self.xml_file)
                    articles = re.findall(r'(\*\*\[\[.*?\]\]\*\*.*?)(?=\*\*\[\[.*?\]\]\*\*|$)', full_markdown, flags=re.S)
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
                    pattern = self.filename_pattern.get().replace("{prefix}", self.config['article_prefix']).replace("{code}", self.config['code_name'])
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
    root = tk.Tk()
    app = SwissCodeGUI(root)
    root.mainloop()
