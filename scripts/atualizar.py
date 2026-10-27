#!/usr/bin/env python3
"""Atualiza a lista do Radar de Concursos a partir do PCI Concursos.

Roda todo dia pelo GitHub Actions (veja .github/workflows/atualizar.yml).
Lê os concursos abertos do Ceará e os nacionais, abre a notícia de cada um e
guarda os que têm cargo administrativo de nível médio.

Regras:
- Ceará: entra qualquer salário (o piso é R$ 1.621).
- Nacionais: só com salário a partir de R$ 2.500 e vagas no Ceará ou no país todo.
- Concursos de banco e seleções temporárias ficam de fora.
- Concursos adicionados à mão (origem "manual") nunca são apagados; o script só
  avisa quando o prazo de inscrição deles muda no PCI.

Uso:
    python3 scripts/atualizar.py            # busca e grava site/dados/*.json
    python3 scripts/atualizar.py --simular  # busca e só mostra o resultado
"""
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "site" / "dados"
ARQ_CONCURSOS = DADOS / "concursos.json"
ARQ_META = DADOS / "meta.json"

FONTES = [
    ("https://www.pciconcursos.com.br/concursos/nordeste/", "CE"),
    ("https://www.pciconcursos.com.br/concursos/nacional/", "NACIONAL"),
]
USER_AGENT = "RadarConcursos/1.0 (uso pessoal; uma busca por dia)"
PAUSA = 2.0  # segundos entre requisições, para não sobrecarregar o site
SALARIO_MIN_NACIONAL = 2500
DIAS_NOVO = 7  # dias que um concurso fica com a etiqueta "Novo"
FUSO = timezone(timedelta(hours=-3))  # Fortaleza

# Cargos administrativos procurados no texto da notícia (sem acento, minúsculo).
CARGOS_ADM = {
    "agente administrativo": "Agente Administrativo",
    "assistente administrativo": "Assistente Administrativo",
    "auxiliar administrativo": "Auxiliar Administrativo",
    "assistente em administracao": "Assistente em Administração",
    "assistente de administracao": "Assistente de Administração",
    "agente de administracao": "Agente de Administração",
    "tecnico administrativo": "Técnico Administrativo",
    "oficial administrativo": "Oficial Administrativo",
    "secretario escolar": "Secretário Escolar",
    "agente patrimonial": "Agente Patrimonial",
    "assistente de arquivo": "Assistente de Arquivo",
    "escriturario": "Escriturário",
    "tecnico legislativo": "Técnico Legislativo",
    "tecnico judiciario": "Técnico Judiciário",
    "tecnico do seguro social": "Técnico do Seguro Social",
    "tecnico ministerial": "Técnico Ministerial",
    "tecnico da defensoria": "Técnico da Defensoria",
    "atendente comercial": "Atendente Comercial",
}

BANCOS = re.compile(
    r"\bbanco\b|caixa economica|\bbnb\b|bndes|banrisul|banese|banpara|\bbrb\b|\bbasa\b"
    r"|banestes|sicoob|sicredi|cooperativa de credito"
)
SELECAO = re.compile(r"selecao|seletivo|professores? substitutos?")
PAIS_TODO = (
    "ceara", "todo o pais", "todo o brasil", "todos os estados", "todas as regioes",
    "ambito nacional", "todas as unidades da federacao", "todas as capitais",
)

# Sites de bancas organizadoras -> nome mostrado no painel.
BANCAS = {
    "universidadepatativa": "Universidade Patativa do Assaré",
    "cetrede": "Fundação Cetrede",
    "idib": "IDIB",
    "consulpam": "Instituto Consulpam",
    "fcpc": "FCPC",
    "seletiva": "Instituto Seletiva",
    "inbrasp": "Inbrasp",
    "idecan": "Idecan",
    "uece": "CEV/Uece",
    "cebraspe": "Cebraspe",
    "fgv": "FGV",
    "concursosfcc": "FCC",
    "institutoaocp": "Instituto AOCP",
    "quadrix": "Quadrix",
    "ibfc": "IBFC",
    "vunesp": "Vunesp",
    "avalia": "Instituto Avalia",
    "consulplan": "Instituto Consulplan",
    "ibgpconcursos": "IBGP",
    "ibade": "IBADE",
    "cesgranrio": "Cesgranrio",
    "fundatec": "Fundatec",
    "idcap": "IDCAP",
}

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

MATERIAS_PADRAO = [
    "Língua Portuguesa",
    "Matemática e Raciocínio Lógico",
    "Noções de Informática",
    "Conhecimentos Gerais",
    "Rotinas Administrativas",
    "Noções de Direito Administrativo",
]


# ---------- utilidades ----------

def hoje():
    return datetime.now(FUSO).date()


def sem_acento(texto):
    nfd = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def limpar_html(trecho):
    trecho = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", trecho, flags=re.S)
    trecho = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", trecho)
    trecho = html.unescape(re.sub(r"<[^>]+>", "", trecho))
    linhas = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in trecho.split("\n")]
    return "\n".join(l for l in linhas if l)


def valor(texto):
    return float(texto.replace(".", "").replace(",", "."))


def iso(d):
    return d.isoformat() if d else None


def slug(texto):
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(texto)).strip("-")[:60]


def chave_orgao(nome):
    n = sem_acento(nome)
    n = n.replace("prefeitura municipal de", "prefeitura de").replace("camara municipal de", "camara de")
    n = re.sub(r"\s+-\s+(ce|ceara)\b.*$", "", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def baixar(url):
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(pedido, timeout=30) as resp:
        dados = resp.read()
    time.sleep(PAUSA)
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError:
        return dados.decode("cp1252", errors="replace")


# ---------- datas em português ----------

PADRAO_DATAS = re.compile(
    r"(?P<num>\d{1,2})/(?P<mesn>\d{1,2})/(?P<anon>\d{4})"
    r"|(?P<d1>\d{1,2})º?(?:\s+a\s+(?P<d2>\d{1,2})º?)?\s+de\s+(?P<mes>[a-zç]+)(?:\s+de\s+(?P<ano>\d{4}))?"
)


def datas_no_texto(texto, ano_padrao):
    """Todas as datas citadas no texto, na ordem em que aparecem."""
    achadas = []
    for m in PADRAO_DATAS.finditer(sem_acento(texto)):
        try:
            if m.group("num"):
                achadas.append(date(int(m.group("anon")), int(m.group("mesn")), int(m.group("num"))))
                continue
            mes = MESES.get(m.group("mes"))
            if not mes:
                continue
            ano = int(m.group("ano")) if m.group("ano") else ano_padrao
            achadas.append(date(ano, mes, int(m.group("d1"))))
            if m.group("d2"):
                achadas.append(date(ano, mes, int(m.group("d2"))))
        except ValueError:
            continue
    return achadas


def frases(texto):
    for linha in texto.split("\n"):
        for f in re.split(r"(?<=[.!?])\s+", linha):
            if f.strip():
                yield f.strip()


# ---------- leitura do PCI ----------

ITEM = re.compile(r'data-url="(?P<url>[^"]+)"(?P<corpo>.*?)<div class="clear">', re.S)


def secao(pagina, sec_id):
    inicio = pagina.find(f'id="{sec_id}" class="ua"')
    if inicio < 0:
        return None
    fim = pagina.find('class="ua"', inicio + 30)
    return pagina[inicio: fim if fim > 0 else len(pagina)]


def ler_lista(trecho):
    itens = []
    for m in ITEM.finditer(trecho):
        corpo = m.group("corpo")
        link = re.search(r'<a [^>]*title="([^"]*)"[^>]*>(.*?)</a>', corpo, re.S)
        cd = re.search(r'<div class="cd">(.*?)</div>', corpo, re.S)
        ce = re.search(r'<div class="ce">(.*?)</div>', corpo, re.S)
        uf = re.search(r'<div class="cc">(.*?)</div>', corpo, re.S)
        if not (link and cd):
            continue
        partes = [limpar_html(p) for p in re.split(r"<br\s*/?>", cd.group(1))]
        partes += [""] * (3 - len(partes))
        sal = re.search(r"R\$\s*([\d.]+,\d{2})", partes[0])
        datas = re.findall(r"(\d{2})/(\d{2})/(\d{4})", limpar_html(ce.group(1)) if ce else "")
        fim = None
        if datas:
            d, mth, a = datas[-1]
            fim = date(int(a), int(mth), int(d))
        itens.append({
            "url": m.group("url"),
            "titulo": html.unescape(link.group(1)),
            "nome": limpar_html(link.group(2)),
            "uf": limpar_html(uf.group(1)) if uf else "",
            "vagas": re.sub(r"\s*até\s+R\$.*", "", partes[0]).strip() or None,
            "salarioMax": valor(sal.group(1)) if sal else None,
            "cargos": partes[1],
            "niveis": [n.strip() for n in partes[2].split("/") if n.strip()],
            "inscricoesFim": fim,
        })
    return itens


def texto_noticia(pagina):
    inicio, fim = pagina.find("<article"), pagina.find("</article>")
    corpo = pagina[inicio:fim] if inicio >= 0 and fim > inicio else pagina
    texto = limpar_html(corpo)
    return "\n".join(l for l in texto.split("\n") if "podcast" not in l.lower())


TAXA_MEDIO = re.compile(
    r"r\$\s*([\d.]+(?:,\d{2})?)\s+para\s+(?:o\s+|os\s+cargos\s+de\s+)?(?:nivel\s+)?medio"
)


def analisar_noticia(item, texto):
    """Extrai do texto da notícia os dados que o painel usa."""
    norm = sem_acento(texto)
    ano = (item["inscricoesFim"] or hoje()).year
    achados = [(p, nome) for p, nome in CARGOS_ADM.items() if p in norm]
    if not achados:
        return None

    vagas_total, salarios = 0, []
    linhas = texto.split("\n")
    for padrao, _ in achados:
        for linha in linhas:
            ln = sem_acento(linha)
            if padrao in ln and len(linha) < 100:
                v = re.search(r"(\d+)\s+vagas?", ln)
                if v:
                    vagas_total += int(v.group(1))
                s = re.search(r"r\$\s*([\d.]+,\d{2})", ln)
                if s:
                    salarios.append(valor(s.group(1)))
                break

    inicio, prova, taxa, site = None, None, None, None
    for f in frases(texto):
        fn = sem_acento(f)
        if inicio is None and "inscri" in fn:
            ds = datas_no_texto(f, ano)
            if len(ds) >= 2:
                inicio = ds[0]
        if site is None and "inscri" in fn:
            w = re.search(r"((?:https?://)?www\.[^\s,;)]+)", f)
            if w:
                site = w.group(1).rstrip(".")
        if prova is None and "prova" in fn and "titulo" not in fn and "aptidao" not in fn:
            ds = [d for d in datas_no_texto(f, ano) if not item["inscricoesFim"] or d >= item["inscricoesFim"]]
            if ds:
                prova = ds[0]
        if taxa is None:
            t = TAXA_MEDIO.search(fn)
            if t:
                taxa = valor(t.group(1))

    if site and not site.startswith("http"):
        site = "https://" + site
    banca = None
    for chave, nome in BANCAS.items():
        if (site and chave in site.lower()) or re.search(rf"\b{chave}\b", norm):
            banca = nome
            break

    faixa = re.search(r"r\$\s*([\d.]+,\d{2})\s+a\s+r\$\s*([\d.]+,\d{2})", norm)
    if len(salarios) == 1:
        salario, obs = salarios[0], None
    else:
        salario = None
        if faixa:
            obs = f"faixa do edital: R$ {faixa.group(1)} a R$ {faixa.group(2)}"
        elif item["salarioMax"]:
            obs = f"até R$ {item['salarioMax']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            obs = None

    nomes = [nome for _, nome in achados]
    alerta = None
    if any("fundamental" in sem_acento(n) for n in item["niveis"]) and any(n.startswith("Auxiliar") for n in nomes):
        alerta = "Auxiliar Administrativo às vezes é de nível fundamental. Confira no edital."

    return {
        "cargo": ", ".join(nomes),
        "vagas": f"{vagas_total}" if vagas_total else item["vagas"],
        "salario": salario,
        "salarioObs": obs,
        "banca": banca,
        "taxa": taxa,
        "inscricoesInicio": iso(inicio),
        "prova": iso(prova),
        "link": site,
        "alerta": alerta,
    }


def buscar():
    """Lê as listas do PCI e devolve os concursos que passam nos critérios."""
    resultado = []
    for url, sec_id in FONTES:
        pagina = baixar(url)
        trecho = secao(pagina, sec_id)
        if trecho is None:
            raise RuntimeError(f"Não achei a seção {sec_id} em {url}. O layout do site pode ter mudado.")
        itens = ler_lista(trecho)
        if not itens:
            raise RuntimeError(f"A seção {sec_id} de {url} veio sem concursos. O layout do site pode ter mudado.")
        print(f"{sec_id}: {len(itens)} itens na lista")
        for item in itens:
            titulo_n = sem_acento(item["titulo"] + " " + item["nome"])
            if not any("medio" in sem_acento(n) for n in item["niveis"]):
                continue
            if BANCOS.search(titulo_n) or SELECAO.search(titulo_n):
                continue
            nacional = sec_id == "NACIONAL"
            if nacional and item["salarioMax"] is not None and item["salarioMax"] < SALARIO_MIN_NACIONAL:
                continue
            try:
                texto = texto_noticia(baixar(item["url"]))
            except Exception as erro:  # uma notícia fora do ar não derruba a busca toda
                print(f"  ! não consegui abrir {item['url']}: {erro}")
                continue
            if nacional and not any(p in sem_acento(texto) for p in PAIS_TODO):
                continue
            dados = analisar_noticia(item, texto)
            if not dados:
                continue
            orgao, _, orgao_nome = item["nome"].partition(" - ")
            dados.update({
                "orgao": orgao.strip(),
                "orgaoNome": orgao_nome.strip() or None,
                "uf": "BR" if nacional else "CE",
                "esfera": "federal" if nacional else ("municipal" if re.search(r"prefeitura|camara", titulo_n) else "estadual"),
                "inscricoesFim": iso(item["inscricoesFim"]),
                "fonte": item["url"],
            })
            print(f"  + {dados['orgao']}: {dados['cargo']} (até {dados['inscricoesFim']})")
            resultado.append(dados)
    return resultado


# ---------- juntar com a lista atual ----------

def juntar(existentes, achados, dia):
    por_chave = {chave_orgao(c["orgao"]): c for c in existentes if not c.get("arquivado")}
    ids = {c["id"] for c in existentes}
    novos, mudados = [], []
    for a in achados:
        atual = por_chave.get(chave_orgao(a["orgao"]))
        if atual is None:
            base = f"pci-{slug(a['orgao'])}-{(a['inscricoesFim'] or iso(dia))[:4]}"
            novo_id, n = base, 2
            while novo_id in ids:
                novo_id, n = f"{base}-{n}", n + 1
            ids.add(novo_id)
            doc = {
                "id": novo_id, "origem": "pci", "tipo": "aberto", "cidade": None,
                "regime": None, "jornada": None, "provaObs": None, "pagamentoAte": None,
                "observacao": "Encontrado automaticamente no PCI Concursos. Confira os detalhes no edital.",
                "materias": MATERIAS_PADRAO, "materiasFonte": "tipicas", "conferir": True,
                "adicionadoEm": iso(dia), "atualizadoEm": iso(dia), "arquivado": False,
                **a,
            }
            existentes.append(doc)
            por_chave[chave_orgao(a["orgao"])] = doc
            novos.append(doc)
            continue
        if atual.get("origem") == "manual":
            # Dados curados à mão: só avisa quando o prazo muda.
            if a["inscricoesFim"] and a["inscricoesFim"] != atual.get("inscricoesFim"):
                atual["inscricoesFim"] = a["inscricoesFim"]
                atual["alerta"] = f"Prazo de inscrição atualizado para {fmt(a['inscricoesFim'])} (PCI Concursos)."
                atual["atualizadoEm"] = iso(dia)
                mudados.append(atual)
            continue
        mudou = False
        for campo in ("inscricoesFim", "inscricoesInicio", "prova", "salario", "salarioObs", "vagas", "taxa", "banca", "link", "cargo"):
            if a.get(campo) is not None and a[campo] != atual.get(campo):
                if campo == "inscricoesFim":
                    atual["alerta"] = f"Prazo de inscrição mudou para {fmt(a[campo])}."
                atual[campo] = a[campo]
                mudou = True
        if mudou:
            atual["atualizadoEm"] = iso(dia)
            mudados.append(atual)
    return novos, mudados


def arquivar(concursos, dia):
    arquivados = []
    for c in concursos:
        if c.get("arquivado") or c.get("tipo") == "previsto":
            continue
        prova = date.fromisoformat(c["prova"]) if c.get("prova") else None
        fim = date.fromisoformat(c["inscricoesFim"]) if c.get("inscricoesFim") else None
        if (prova and prova < dia - timedelta(days=30)) or (not prova and fim and fim < dia - timedelta(days=60)):
            c["arquivado"] = True
            c["atualizadoEm"] = iso(dia)
            arquivados.append(c)
    return arquivados


def fmt(iso_data):
    d = date.fromisoformat(iso_data)
    return f"{d.day:02d}/{d.month:02d}"


def resumo(concursos, novos, mudados, dia):
    partes = []
    if novos:
        nomes = ", ".join(c["orgao"] for c in novos[:4]) + (" e outros" if len(novos) > 4 else "")
        verbo, plural = ("entraram", "s") if len(novos) > 1 else ("entrou", "")
        partes.append(f"Nos últimos {DIAS_NOVO} dias {verbo} {len(novos)} concurso{plural}: {nomes}.")
    else:
        partes.append(f"Nenhum concurso novo nos últimos {DIAS_NOVO} dias.")
    if mudados:
        partes.append("Prazo atualizado: " + ", ".join(f"{c['orgao']} ({fmt(c['inscricoesFim'])})" for c in mudados[:3]) + ".")
    fechando = sorted(
        (c for c in concursos
         if not c.get("arquivado") and c.get("inscricoesFim")
         and 0 <= (date.fromisoformat(c["inscricoesFim"]) - dia).days <= 7),
        key=lambda c: c["inscricoesFim"],
    )
    if fechando:
        partes.append("Terminam nos próximos 7 dias: " + ", ".join(f"{c['orgao']} ({fmt(c['inscricoesFim'])})" for c in fechando[:4]) + ".")
    return " ".join(partes)


def novos_recentes(concursos, novos, dia):
    """Concursos que entraram nos últimos DIAS_NOVO dias e o dia em que cada um entrou.

    A data fica no meta.json, e não no adicionadoEm do concurso, porque os
    concursos colocados à mão no começo também têm adicionadoEm recente."""
    desde = {}
    if ARQ_META.exists():
        anterior = json.loads(ARQ_META.read_text(encoding="utf-8"))
        desde = anterior.get("novosDesde") or {i: anterior["ultimaAtualizacao"] for i in anterior.get("novos", [])}
    for c in novos:
        desde[c["id"]] = iso(dia)
    ativos = {c["id"] for c in concursos if not c.get("arquivado")}
    desde = {i: d for i, d in desde.items()
             if i in ativos and (dia - date.fromisoformat(d)).days < DIAS_NOVO}
    return [c for c in concursos if c["id"] in desde], desde


def main():
    simular = "--simular" in sys.argv
    dia = hoje()
    concursos = json.loads(ARQ_CONCURSOS.read_text(encoding="utf-8"))
    achados = buscar()
    novos, mudados = juntar(concursos, achados, dia)
    arquivados = arquivar(concursos, dia)
    recentes, desde = novos_recentes(concursos, novos, dia)
    meta = {
        "ultimaAtualizacao": iso(dia),
        "proximaAtualizacao": iso(dia + timedelta(days=1)),
        "novos": [c["id"] for c in recentes],
        "novosDesde": desde,
        "resumo": resumo(concursos, recentes, mudados, dia),
        "fonte": "PCI Concursos",
    }
    print(f"\nNovos: {len(novos)} · prazos mudados: {len(mudados)} · arquivados: {len(arquivados)}")
    print("Resumo:", meta["resumo"])
    if simular:
        print(json.dumps(novos, ensure_ascii=False, indent=1))
        return
    ARQ_CONCURSOS.write_text(json.dumps(concursos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ARQ_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
