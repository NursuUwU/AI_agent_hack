// Сборка описания работы: docs/submission/report.md → HTML (шрифт Onest) → PDF в Chromium.
// Перенесено из отчёта команды по треку «Вайб-кодинг»; ядро агента (tools/pf.py) от Node не зависит.
//   cd scripts/report && npm install && npx playwright install chromium   # один раз
//   node build.mjs   → docs/submission/report.pdf (+ report.html рядом с исходником — для просмотра в браузере)
//
// Markdown — небольшое подмножество: # ## ###, абзацы, списки (вложенные — отступ 2 пробела), > цитаты, таблицы,
// ``` код, --- разрыв страницы, строка <div …> — HTML как есть, ![подпись](путь) — рисунок, ::: gallery N / ::: note / ::: todo / ::: prompt … ::: — блоки.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

// fileURLToPath/pathToFileURL — чтобы пути работали и в Windows (C:\...), не только в Linux.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const SRC = path.join(ROOT, "docs/submission/report.md");
const HTML = path.join(ROOT, "docs/submission/report.html");
const PDF = path.join(ROOT, "docs/submission/report.pdf");
const FONTS = path.join(ROOT, "assets/fonts/onest");
const url = (p) => pathToFileURL(p).href;
const TITLE = "Стратег Фабрики продуктов: описание работы";
const FOOTER = "Стратег Фабрики продуктов · описание работы ИИ-агента · только открытые данные";

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function inline(s) {
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(esc(c)) - 1}\u0000`);
  s = esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${u}">${t}</a>`)
    // Голые адреса — тоже ссылки (в PDF кликабельны).
    .replace(/(^|[\s(«])(https?:\/\/[^\s<)»,;]+[^\s<)»,;.])/g, '$1<a href="$2">$2</a>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

function figure(line) {
  const m = /^!\[(.*)\]\(([^)\s]+)\)(?:\{([^}]*)\})?$/.exec(line.trim());
  if (!m) throw new Error(`плохая картинка: ${line}`);
  const [, caption, src, cls = ""] = m;
  if (!fs.existsSync(path.join(ROOT, "docs/submission", src))) throw new Error(`нет файла ${src}`);
  return `<figure class="${cls.replace(/\./g, " ").trim()}"><img src="${src}" alt=""><figcaption>${inline(caption)}</figcaption></figure>`;
}

function list(lines) {
  // Вложенность по отступу 2 пробела; маркер «- » или «1. ».
  let html = "";
  const stack = [];
  for (const raw of lines) {
    const depth = Math.floor(raw.match(/^ */)[0].length / 2);
    const ordered = /^\s*\d+\.\s/.test(raw);
    const text = raw.replace(/^\s*(-|\d+\.)\s/, "");
    while (stack.length > depth + 1) html += `</li></${stack.pop()}>`;
    if (stack.length === depth + 1) html += "</li>";
    else {
      stack.push(ordered ? "ol" : "ul");
      html += `<${ordered ? "ol" : "ul"}>`;
    }
    html += `<li>${inline(text)}`;
  }
  while (stack.length) html += `</li></${stack.pop()}>`;
  return html;
}

function table(lines) {
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const [head, , ...rows] = lines;
  return `<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}

function render(md) {
  const lines = md.split("\n");
  let out = "";
  let i = 0;
  const isBlockStart = (l) => /^(#{1,3} |- |\s*\d+\. |> |\||```|---$|!\[|:::)/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) {
      i++;
    } else if (/^<(div|section|figure)\b/.test(l)) {
      out += l; // строка HTML — как есть (например, схема процесса)
      i++;
    } else if (l.startsWith("<!--")) {
      while (i < lines.length && !lines[i].includes("-->")) i++;
      i++;
    } else if (/^#{1,3} /.test(l)) {
      const level = l.match(/^#+/)[0].length;
      const text = l.replace(/^#+ /, "");
      const id = text.match(/\{#([\w-]+)\}$/)?.[1];
      out += `<h${level}${id ? ` id="${id}"` : ""}>${inline(text.replace(/\s*\{#[\w-]+\}$/, ""))}</h${level}>`;
      i++;
    } else if (l === "---") {
      out += `<div class="page-break"></div>`;
      i++;
    } else if (l.startsWith("```")) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      out += `<pre>${esc(body.join("\n"))}</pre>`;
    } else if (l.startsWith(":::")) {
      const [, kind, arg] = l.match(/^:::\s*(\w+)\s*(.*)$/);
      const body = [];
      i++;
      while (i < lines.length && lines[i].trim() !== ":::") body.push(lines[i++]);
      i++;
      if (kind === "gallery") {
        out += `<div class="gallery cols-${arg || 3}">${body.filter((b) => b.trim()).map(figure).join("")}</div>`;
      } else {
        const [title, ...rest] = kind === "prompt" || kind === "todo" || kind === "note" ? [arg, ...body] : ["", ...body];
        out += `<div class="box ${kind}">${title ? `<div class="box-title">${inline(title)}</div>` : ""}${render(rest.join("\n"))}</div>`;
      }
    } else if (l.startsWith("![")) {
      out += figure(l);
      i++;
    } else if (l.startsWith("|")) {
      const t = [];
      while (i < lines.length && lines[i].startsWith("|")) t.push(lines[i++]);
      out += table(t);
    } else if (l.startsWith("> ")) {
      const q = [];
      while (i < lines.length && lines[i].startsWith(">")) q.push(lines[i++].replace(/^> ?/, ""));
      out += `<blockquote>${render(q.join("\n"))}</blockquote>`;
    } else if (/^(\s*)(- |\d+\. )/.test(l)) {
      const items = [];
      while (i < lines.length && /^(\s*)(- |\d+\. )/.test(lines[i])) {
        let item = lines[i++];
        // Продолжение пункта на следующих строках (с отступом, без маркера).
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*(- |\d+\. )/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      out += list(items);
    } else {
      const p = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) p.push(lines[i++].trim());
      out += `<p>${inline(p.join(" "))}</p>`;
    }
  }
  return out;
}

const css = `
@font-face { font-family: Onest; src: url("${url(path.join(FONTS, "Onest-Regular.ttf"))}"); font-weight: 400; }
@font-face { font-family: Onest; src: url("${url(path.join(FONTS, "Onest-SemiBold.ttf"))}"); font-weight: 600; }
@page { size: A4; margin: 16mm 15mm 17mm; }
:root { --brand: #0b6e63; --ink: #171b2b; --ink2: #4c5570; --ink3: #6b7390; --hair: #dfe3ef; --page: #f1f5f4; --soft: #e8f3f1; --warn: #fdf3df; --warn-ink: #8a5300; }
* { box-sizing: border-box; }
html { font-family: Onest, sans-serif; color: var(--ink); font-size: 10.2pt; line-height: 1.5; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; }
h1 { font-size: 30pt; line-height: 1.08; color: var(--ink); margin: 0 0 8mm; font-weight: 600; letter-spacing: -0.01em; }
h2 { font-size: 19pt; line-height: 1.15; color: var(--brand); margin: 6mm 0 5mm; font-weight: 600; break-after: avoid; }
#demo { break-before: page; margin-top: 0; }
h3 { font-size: 12.5pt; margin: 6mm 0 2.5mm; font-weight: 600; break-after: avoid; }
p { margin: 0 0 3mm; orphans: 3; widows: 3; }
a { color: var(--brand); text-decoration: none; }
strong { font-weight: 600; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: var(--page); border-radius: 3px; padding: 0 2px; }
ul, ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin: 0 0 1.2mm; }
li > ul, li > ol { margin: 1.2mm 0 0; }
blockquote { margin: 0 0 3mm; padding: 2.5mm 4mm; border-radius: 3mm; background: var(--soft); color: var(--ink); break-inside: avoid; }
blockquote p:last-child { margin: 0; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 7.8pt; line-height: 1.45; background: var(--page); border: 1px solid var(--hair); border-radius: 3mm; padding: 3mm 4mm; white-space: pre-wrap; break-inside: avoid; margin: 0 0 3mm; }
table { width: 100%; border-collapse: collapse; margin: 0 0 4mm; font-size: 9pt; break-inside: auto; }
th { text-align: left; font-weight: 600; color: var(--ink2); border-bottom: 1.5px solid var(--ink2); padding: 1.5mm 2mm; }
td { border-bottom: 1px solid var(--hair); padding: 1.6mm 2mm; vertical-align: top; }
tr { break-inside: avoid; }
figure { margin: 0 0 4mm; break-inside: avoid; }
figure img { display: block; width: 100%; border-radius: 2.5mm; border: 1px solid var(--hair); }
figcaption { font-size: 8.4pt; color: var(--ink3); margin-top: 1.5mm; line-height: 1.35; }
figure.half { width: 60%; }
figure.narrow { width: 42%; }
.gallery { display: grid; gap: 4mm; margin: 0 0 4mm; align-items: start; }
.gallery figure { margin: 0; }
.gallery.cols-2 { grid-template-columns: repeat(2, 1fr); }
.gallery.cols-3 { grid-template-columns: repeat(3, 1fr); }
.gallery.cols-4 { grid-template-columns: repeat(4, 1fr); gap: 3mm; }
.gallery.phone img { aspect-ratio: 390 / 700; object-fit: cover; object-position: top; }
.box { border-radius: 3mm; padding: 3.5mm 4.5mm; margin: 0 0 4mm; break-inside: avoid; }
.box > :last-child { margin-bottom: 0; }
.box-title { font-weight: 600; margin-bottom: 1.5mm; }
.box.note { background: var(--soft); }
.box.note ol { columns: 2; column-gap: 8mm; margin: 0; }
.box.note li { break-inside: avoid; margin-bottom: 0.6mm; }
.box.prompt { background: #fff; border: 1px solid var(--hair); }
.box.prompt .box-title { color: var(--brand); font-size: 9pt; }
.box.todo { background: var(--warn); border: 1.5px dashed #d9a441; color: var(--warn-ink); }
.page-break { break-after: page; }
.cover { height: 262mm; display: flex; flex-direction: column; justify-content: space-between; break-after: page; }
.cover .mark { width: 22mm; height: 22mm; }
.cover .kicker { color: var(--brand); font-weight: 600; font-size: 11pt; margin: 10mm 0 4mm; }
.cover .lead { font-size: 13pt; color: var(--ink2); max-width: 150mm; }
.cover .facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4mm; margin: 8mm 0; }
.cover .fact { background: var(--page); border-radius: 3mm; padding: 3.5mm; }
.cover .fact b { display: block; font-size: 20pt; font-weight: 600; color: var(--brand); line-height: 1.1; }
.cover .fact span { font-size: 8.4pt; color: var(--ink2); }
.cover .meta { font-size: 9.5pt; color: var(--ink2); }
.cover .meta td { border: 0; padding: 0.8mm 0; }
.cover .meta td:first-child { color: var(--ink3); width: 32mm; }
.disclaimer { font-size: 8.2pt; color: var(--ink3); border-top: 1px solid var(--hair); padding-top: 3mm; }
.toc ol { columns: 2; column-gap: 10mm; padding-left: 5mm; }
.toc li { break-inside: avoid; }
.flow { display: flex; flex-wrap: wrap; gap: 2mm; align-items: center; margin: 0 0 4mm; font-size: 8.8pt; }
.flow span { background: var(--soft); border-radius: 2mm; padding: 1.5mm 2.5mm; }
.flow i { color: var(--ink3); font-style: normal; }
`;

const md = fs.readFileSync(SRC, "utf8");
// Обложка — отдельный блок HTML в начале исходника (между <!--cover и cover-->), остальное — Markdown.
const cover = md.match(/<!--cover\n([\s\S]*?)\ncover-->/)?.[1] ?? "";
const body = render(md.replace(/<!--cover\n[\s\S]*?\ncover-->/, ""));
const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${TITLE}</title>
<base href="${url(path.join(ROOT, "docs/submission"))}/"><style>${css}</style></head><body>${cover}${body}</body></html>`;
fs.writeFileSync(HTML, html);

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(url(HTML), { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.pdf({
  path: PDF,
  format: "A4",
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: "<span></span>",
  footerTemplate: `<div style="width:100%;font-size:7pt;color:#6b7390;padding:0 15mm;display:flex;justify-content:space-between;font-family:sans-serif">
    <span>${FOOTER}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
  margin: { top: "16mm", bottom: "17mm", left: "15mm", right: "15mm" },
});
await browser.close();
const kb = Math.round(fs.statSync(PDF).size / 1024);
console.log(`PDF: ${path.relative(ROOT, PDF)}, ${kb} КБ`);
