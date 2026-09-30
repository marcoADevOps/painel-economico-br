"""Builds the portfolio PDF (docs/painel-economico-br.pdf) with ReportLab.

    python docs/build_pdf.py [output.pdf]

Uses the images in docs/img (architecture diagram and dashboard screenshots).
Fonts: Segoe UI from Windows (full Portuguese/typographic glyph coverage);
falls back to Helvetica elsewhere.
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs" / "img"
OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "painel-economico-br.pdf"
REPO_URL = "github.com/marcoADevOps/painel-economico-br"

# --- fonts and styles -----------------------------------------------------------------

FONT_DIR = Path("C:/Windows/Fonts")
if (FONT_DIR / "segoeui.ttf").exists():
    pdfmetrics.registerFont(TTFont("Body", FONT_DIR / "segoeui.ttf"))
    pdfmetrics.registerFont(TTFont("Body-Bold", FONT_DIR / "segoeuib.ttf"))
    pdfmetrics.registerFont(TTFont("Body-Italic", FONT_DIR / "segoeuii.ttf"))
    pdfmetrics.registerFont(TTFont("Mono", FONT_DIR / "consola.ttf"))
    pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic")
    BODY, BOLD, MONO = "Body", "Body-Bold", "Mono"
else:
    BODY, BOLD, MONO = "Helvetica", "Helvetica-Bold", "Courier"

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
ACCENT = colors.HexColor("#2563eb")
LIGHT = colors.HexColor("#eff6ff")
GRID = colors.HexColor("#cbd5e1")

S = {
    "cover_title": ParagraphStyle("ct", fontName=BOLD, fontSize=30, leading=36, textColor=INK),
    "cover_sub": ParagraphStyle("cs", fontName=BODY, fontSize=14, leading=20, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=18, leading=24, textColor=ACCENT,
                         spaceBefore=6, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=13, leading=18, textColor=INK,
                         spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("b", fontName=BODY, fontSize=10.2, leading=15, textColor=INK,
                           spaceAfter=6),
    "bullet": ParagraphStyle("bl", fontName=BODY, fontSize=10.2, leading=15, textColor=INK,
                             leftIndent=14, bulletIndent=2, spaceAfter=3),
    "cell": ParagraphStyle("c", fontName=BODY, fontSize=8.8, leading=12, textColor=INK),
    "cell_b": ParagraphStyle("cb", fontName=BOLD, fontSize=8.8, leading=12, textColor=colors.white),
    "caption": ParagraphStyle("cap", fontName=BODY, fontSize=8.5, leading=11, textColor=MUTED,
                              alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=8.8, leading=12, textColor=INK,
                           backColor=colors.HexColor("#f1f5f9"), borderPadding=6,
                           spaceBefore=4, spaceAfter=10),
    "metric": ParagraphStyle("m", fontName=BOLD, fontSize=20, leading=24, textColor=ACCENT,
                             alignment=TA_CENTER),
    "metric_l": ParagraphStyle("ml", fontName=BODY, fontSize=8.5, leading=11, textColor=MUTED,
                               alignment=TA_CENTER),
}

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm
FRAME_W = PAGE_W - 2 * MARGIN


def p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(item, S["bullet"], bulletText="•") for item in items]


def table(rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(c, S["cell_b"]) for c in rows[0]]]
    data += [[Paragraph(c, S["cell"]) for c in row] for row in rows[1:]]
    t = Table(data, colWidths=[w * FRAME_W for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def image(name: str, caption: str, max_h: float = 21 * cm, max_px: int = 2000) -> KeepTogether:
    """Image scaled to the frame width (or max height), re-encoded as JPEG to keep the PDF light."""
    with PILImage.open(IMG / name) as src:
        img = src.convert("RGB")
        if img.width > max_px:
            img = img.resize((max_px, round(img.height * max_px / img.width)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        w, h = img.size
    buf.seek(0)
    width = FRAME_W
    height = width * h / w
    if height > max_h:
        height, width = max_h, max_h * w / h
    return KeepTogether([Image(buf, width=width, height=height), p(caption, "caption")])


def metrics(items: list[tuple[str, str]]) -> Table:
    cells = [[p(value, "metric"), p(label, "metric_l")] for value, label in items]
    row = [Table([[v], [lab]], colWidths=[FRAME_W / len(items) - 6]) for v, lab in cells]
    t = Table([row], colWidths=[FRAME_W / len(items)] * len(items))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, GRID),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def on_page(canvas, doc) -> None:
    if doc.page == 1:
        return
    canvas.saveState()
    canvas.setFont(BODY, 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 1.1 * cm, "Painel Econômico BR · documentação do projeto")
    canvas.drawRightString(PAGE_W - MARGIN, 1.1 * cm, f"{doc.page}")
    canvas.setStrokeColor(GRID)
    canvas.line(MARGIN, 1.4 * cm, PAGE_W - MARGIN, 1.4 * cm)
    canvas.restoreState()


# --- content -------------------------------------------------------------------------------


def build() -> None:
    story = []

    # Cover
    story += [
        Spacer(1, 5 * cm),
        p("Painel Econômico BR", "cover_title"),
        Spacer(1, 10),
        p("Pipeline de dados da economia brasileira com Apache Airflow 3, "
          "Postgres, Metabase e CI/CD num homelab Proxmox", "cover_sub"),
        Spacer(1, 1.2 * cm),
        metrics([("4", "DAGs em produção"), ("3", "fontes públicas"),
                 ("12", "checagens de qualidade"), ("115", "testes automatizados")]),
        Spacer(1, 0.6 * cm),
        metrics([("26 anos", "de Selic e câmbio"), ("~600", "municípios com preço semanal"),
                 ("11 mi", "coletas de preço processadas"), ("3", "dashboards como código")]),
        Spacer(1, 3 * cm),
        p(f"Marco · <font color='#2563eb'>{REPO_URL}</font>", "cover_sub"),
        p(f"Documentação gerada em {date.today():%d/%m/%Y}", "cover_sub"),
        PageBreak(),
    ]

    # 1. Overview
    story += [
        p("1. Visão geral", "h1"),
        p("O projeto coleta todo dia indicadores públicos do <b>Banco Central</b> (Selic e dólar "
          "PTAX), do <b>IBGE</b> (IPCA por região e taxa de desocupação) e da <b>ANP</b> (preço dos "
          "combustíveis por município). Os dados passam por três camadas num Postgres (<i>raw</i>, "
          "<i>staging</i> e <i>marts</i>) e são publicados em dashboards no Metabase."),
        p("O ambiente roda 24/7 numa VM do homelab, sem IP público, e é tratado como produção: "
          "deploy automático a cada push, alertas no Telegram, monitoramento externo, backup "
          "verificado e testes de desligamento."),
        p("Objetivos de engenharia", "h2"),
        *bullets([
            "<b>Idempotência:</b> reexecutar qualquer DAG, ou recarregar o histórico inteiro, "
            "não duplica dados (upsert por chave natural, testado em produção).",
            "<b>Resposta bruta primeiro:</b> tudo o que vem das fontes é guardado sem alteração "
            "antes de ser transformado, para permitir auditoria e reprocessamento.",
            "<b>Dado ruim não chega ao usuário:</b> um portão de qualidade roda antes de publicar "
            "a staging.",
            "<b>Janela pela data lógica:</b> o período processado vem da data lógica do Airflow, "
            "nunca de <i>now()</i>. Com isso, o <i>catchup</i> e o backfill funcionam sem código extra.",
            "<b>Nada sensível no Git:</b> o repositório é público; segredos existem só no "
            "<i>.env</i> da VM e são gravados por scripts que não os mostram na tela.",
        ]),
        p("Stack", "h2"),
        table([
            ["Camada", "Tecnologia"],
            ["Orquestração", "Apache Airflow 3.3 (LocalExecutor, TaskFlow API, Assets, mapeamento dinâmico)"],
            ["Armazenamento", "PostgreSQL 16 (metadados, warehouse e configuração do Metabase)"],
            ["Visualização", "Metabase v0.63, só leitura na camada marts"],
            ["Infraestrutura", "Docker Compose numa VM Ubuntu 24.04 (2 vCPU, 4 GB) no Proxmox"],
            ["CI/CD", "GitHub Actions, runner self-hosted, imagens no GHCR"],
            ["Observabilidade", "Telegram, Uptime Kuma (LXC separado), healthchecks"],
            ["Qualidade de código", "ruff, pytest (115 testes), teste de importação dos DAGs"],
        ], [0.25, 0.75]),
        PageBreak(),
    ]

    # 2. Architecture
    story += [
        p("2. Arquitetura", "h1"),
        image("arquitetura.png", "Figura 1: componentes, fluxo de dados e operação.",
              max_h=12 * cm, max_px=2800),
        p("As três DAGs de origem seguem o mesmo fluxo: extração para <i>raw</i>, "
          "transformação para <i>staging</i>, checagens de qualidade e publicação de um "
          "<b>Asset</b>. O DAG <i>marts</i> é disparado quando <b>qualquer</b> fonte publica "
          "(condição OU), de modo que uma fonte com falha não segura as outras. Cada mart é "
          "reconstruído numa única transação."),
        p("O Uptime Kuma e a cópia dos backups ficam <b>fora da VM</b>: se a VM cair, o "
          "monitoramento continua avisando e as cópias continuam disponíveis."),
        PageBreak(),
    ]

    # 3. Sources
    story += [
        p("3. Fontes e DAGs", "h1"),
        table([
            ["DAG", "Fonte e dados", "Agenda", "Histórico"],
            ["bcb_sgs", "API SGS: Selic meta (série 432) e PTAX venda (série 1)",
             "dias úteis 19h", "desde 2000"],
            ["ibge_sidra", "API SIDRA: IPCA (tabelas 1419 e 7060) por região; desocupação "
             "(tabela 4099) por região e UF", "dias úteis 10h", "desde 2012"],
            ["anp_precos", "CSVs da ANP: preço por posto, produto e data; agregado por semana, "
             "município e produto", "segundas 11h", "desde 2016"],
            ["marts", "4 tabelas analíticas a partir da staging", "por Asset", "—"],
        ], [0.15, 0.5, 0.17, 0.18]),
        Spacer(1, 8),
        p("Cada execução agendada reprocessa uma janela para trás (10 dias no BCB, ~13 meses "
          "no IBGE, 4 meses na ANP). Isso cobre dados publicados com atraso ou revisados, e "
          "preenche o período em que a VM ficou desligada. Cargas históricas são disparadas "
          "manualmente com os parâmetros <i>start</i> e <i>end</i>."),
        p("Capacidade e desempenho medidos", "h2"),
        table([
            ["Item", "Medida"],
            ["Carga histórica da ANP (2016–2026)", "47 arquivos, ~1,5 GB baixados, ~11 minutos"],
            ["Pico de memória de uma task da ANP", "~450 MB (no máximo 2 arquivos em paralelo)"],
            ["Staging / marts de combustíveis", "1,17 mi / 1,26 mi linhas; ~11 mi de coletas"],
            ["Banco de dados / arquivos originais", "446 MB / 278 MB (.csv.gz)"],
            ["Backup diário (dump)", "~1,6 MB; os arquivos originais são copiados de forma incremental"],
            ["Metabase", "~1,05 GB (heap 640 MB); limite do contêiner 1,25 GB"],
        ], [0.45, 0.55]),
        PageBreak(),
    ]

    # 4. Data layers and quality
    story += [
        p("4. Camadas de dados e qualidade", "h1"),
        table([
            ["Camada", "Conteúdo", "Garantia"],
            ["raw", "resposta original de cada requisição (JSON) ou arquivo (.csv.gz em disco, "
             "com SHA-256)", "auditoria e reprocessamento"],
            ["staging", "dados tipados e limpos, com chave natural", "upsert idempotente"],
            ["marts", "monthly_indicators, ipca_by_region, unemployment_by_region, "
             "fuel_prices_weekly", "reconstrução numa transação"],
        ], [0.14, 0.58, 0.28]),
        p("Portão de qualidade", "h2"),
        p("Cada fonte roda 4 checagens depois de carregar a staging e <b>antes</b> de publicar "
          "o Asset. Uma violação falha a task na hora, sem novas tentativas (repetir não conserta "
          "dado ruim), envia um alerta com exemplos das linhas problemáticas e mantém os marts na "
          "última versão boa."),
        table([
            ["Checagem", "Regra (exemplos)"],
            ["Duplicados", "nenhuma chave natural repetida"],
            ["Nulos", "colunas obrigatórias preenchidas"],
            ["Faixa plausível", "Selic entre 0 e 50%; PTAX entre R$ 0,50 e R$ 20; IPCA mensal entre "
             "−5% e 10%; média semanal da gasolina entre R$ 0,50 e R$ 20; GLP entre R$ 20 e R$ 300"],
            ["Atraso", "medido em relação à data da execução: 7 dias (BCB), 80 e 240 dias (IBGE), "
             "100 dias (ANP)"],
        ], [0.22, 0.78]),
        PageBreak(),
    ]

    # 5. Dashboards
    story += [p("5. Dashboards", "h1"),
              p("Os dashboards são definidos como código (<i>ops/metabase/provision.py</i>) e "
                "provisionados pela API do Metabase: 3 dashboards e 18 perguntas SQL sobre os marts. "
                "O script é idempotente e executa cada pergunta para detectar erros de SQL."),
              image("dashboard-macro.png", "Figura 2: indicadores macroeconômicos.", max_h=17 * cm),
              PageBreak(),
              image("dashboard-regional.png", "Figura 3: inflação e desemprego por região.",
                    max_h=23 * cm),
              PageBreak(),
              image("dashboard-combustiveis.png", "Figura 4: preços de combustíveis (ANP).",
                    max_h=24 * cm),
              PageBreak()]

    # 6. CI/CD and operations
    story += [
        p("6. CI/CD e operação", "h1"),
        p("Entrega contínua", "h2"),
        *bullets([
            "Em cada push na <i>main</i>: <b>ruff</b> e <b>pytest</b> numa imagem de teste, build "
            "da imagem de produção, publicação no GHCR com a tag do commit e "
            "<i>docker compose up --wait</i> na VM.",
            "O runner self-hosted só roda em push na main. PRs de fork rodam nos runners do GitHub, "
            "porque o repositório é público.",
            "<b>Rollback:</b> trocar <i>AIRFLOW_IMAGE</i> no .env da VM por uma tag anterior.",
        ]),
        p("Confiabilidade", "h2"),
        *bullets([
            "<b>Alertas:</b> task com falha → Telegram, com o link do log, em ~15 minutos "
            "(tentativas após 2, 4 e 8 min); erros de parâmetro avisam na hora.",
            "<b>Uptime Kuma:</b> ping da VM, saúde do scheduler e push do backup. Testado: "
            "scheduler parado → DOWN em 19 s.",
            "<b>Backup:</b> pg_dump diário verificado, com cópia puxada pelo host do Proxmox via "
            "rsync somente leitura; a restauração foi testada.",
            "<b>Desligamento:</b> VM desligada e religada → todos os serviços voltam sozinhos, "
            "com dados idênticos e alertas de DOWN e UP.",
        ]),
        PageBreak(),
    ]

    # 7. Lessons learned
    story += [
        p("7. Lições aprendidas", "h1"),
        p("Cada fonte pública tinha uma armadilha que só apareceu no teste real, não na documentação."),
        table([
            ["Problema", "Onde", "Solução"],
            ["HTTP 200 com página HTML de erro", "BCB", "validar o conteúdo; nova tentativa por janela"],
            ["Carga longa recomeçava do zero a cada falha", "BCB",
             "pular janelas já guardadas: de 40 min de falhas para 1 min"],
            ["ID de localidade repetido entre níveis", "IBGE", "nível territorial na chave natural"],
            ["Accept: application/json → HTTP 401", "ANP (gov.br)", "pedir text/html e */*"],
            ["Mesmos dados em arquivos mensais e semestrais", "ANP",
             "o semestral substitui os mensais (evita dupla contagem)"],
            ["Nomes irregulares, arquivo sem extensão, .zip", "ANP",
             "descobrir os links e classificar pelo nome"],
            ["Downloads de 85 MB cortados", "ANP", "retomar com HTTP Range e conferir o tamanho"],
            ["Provider usa psycopg 3", "Airflow", "SQL só com a DB-API padrão"],
            ["dags test disputando a task com o scheduler", "Airflow",
             "testar DAGs ativos só com execuções normais"],
            ["OutOfMemoryError: Metaspace", "Metabase", "limitar o heap, não o metaspace; swap"],
        ], [0.42, 0.16, 0.42]),
        Spacer(1, 10),
        p("Próximos passos", "h2"),
        *bullets([
            "Dashboards cruzando fontes (combustível × IPCA, câmbio × gasolina, preço × desocupação por UF).",
            "IPCA nacional desde 1979 (tabela SIDRA 1737), para completar o juro real desde 2000.",
            "Expor o dashboard com Cloudflare Tunnel e autenticação.",
        ]),
    ]

    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=2 * cm,
        title="Painel Econômico BR", author="Marco", subject="Documentação do projeto",
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"{OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
