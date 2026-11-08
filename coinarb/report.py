"""Генерация автономного HTML-отчёта (инлайн-стили, без внешних ресурсов)."""
from __future__ import annotations
import html as H
import time
from datetime import datetime


def _fmt(v, unit="₽"):
    if v is None: return "—"
    return f"{v:,.0f}".replace(",", " ") + (f" {unit}" if unit else "")


def _pct(v): return "—" if v is None else f"{v:.1f}%"


def _date(ts):
    return time.strftime("%d.%m.%Y", time.localtime(ts)) if ts else "—"


CSS = """
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#e6e8ee;
font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.4px}
h2{font-size:19px;margin:38px 0 12px;padding-bottom:8px;border-bottom:1px solid #262b36}
h3{font-size:15px;margin:22px 0 8px;color:#aab2c5}
.sub{color:#8b93a7;font-size:13px;margin-bottom:26px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}
.kpi{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:14px 16px}
.kpi .v{font-size:24px;font-weight:600;letter-spacing:-.5px}
.kpi .l{color:#8b93a7;font-size:12px;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0 6px}
th{text-align:left;color:#8b93a7;font-weight:500;font-size:11.5px;text-transform:uppercase;
letter-spacing:.5px;padding:8px 10px;border-bottom:1px solid #262b36}
td{padding:9px 10px;border-bottom:1px solid #1c212a;vertical-align:top}
tr:hover td{background:#151922}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pos{color:#4ade80;font-weight:600}.neg{color:#f87171}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}
.t-ok{background:#14351f;color:#4ade80}.t-warn{background:#3a2d10;color:#fbbf24}
.t-bad{background:#3a1618;color:#f87171}.t-info{background:#152238;color:#60a5fa}
.note{background:#161a22;border-left:3px solid #3b82f6;padding:12px 16px;border-radius:0 8px 8px 0;
margin:14px 0;font-size:13.5px;color:#c3cad8}
.warn{border-left-color:#f59e0b}
.crit{border-left-color:#ef4444}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.bar{height:6px;background:#262b36;border-radius:3px;overflow:hidden;margin-top:5px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#4ade80)}
a{color:#60a5fa;text-decoration:none}a:hover{text-decoration:underline}
.foot{margin-top:44px;color:#5f6678;font-size:12px;border-top:1px solid #262b36;padding-top:16px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:760px){.two{grid-template-columns:1fr}}
"""


def _watch_rows(watch: list[dict], limit: int = 24) -> str:
    rows = []
    for r in watch[:limit]:
        c, e = r["coin"], r["economics"]
        cls = "pos" if e.net_profit > 0 else "neg"
        grade = c.grade_label or ("raw" if not c.is_slabbed else "—")
        badge = ('<span class="tag t-info">уже в слабе</span>' if r["already_slabbed"]
                 else '<span class="tag t-warn">сырая</span>' if not c.is_slabbed
                 else '<span class="tag t-ok">слаб 3-го уровня</span>')
        rows.append(f"""<tr>
<td>{H.escape(c.title[:78])}<div class="mono" style="color:#6b7280;margin-top:3px">
{c.year or '—'} · {H.escape(str(c.denomination or '—'))} · {c.metal or '—'} · {grade}
{H.escape(c.grader or '')}</div></td>
<td class="num">{_fmt(r['buy_price'])}</td>
<td class="num">{_fmt(r['expected_sale'])}</td>
<td class="num">{_fmt(e.slab_cost)}<div class="mono" style="color:#6b7280">{e.tariff_name}</div></td>
<td class="num {cls}">{_fmt(e.net_profit)}</td>
<td class="num {cls}">{_pct(e.roi_pct)}</td>
<td>{badge}<div class="bar"><i style="width:{min(100, int(r['confidence'] * 100))}%"></i></div></td>
</tr>""")
    return "".join(rows) or ('<tr><td colspan="7" style="color:#8b93a7">Нет лотов с аналогами. '
                             'Загрузите comps: <span class="mono">python scan.py import-comps '
                             'data/comps_seed.csv</span></td></tr>')


def _opportunities_html(by_strategy: dict, labels: dict) -> str:
    blocks = []
    order = ["slab_arb", "cross_source", "slab_flip", "underpriced"]
    hints = {
        "slab_arb": "Покупка сырой монеты → слабирование в NGS → продажа в слабе. Проходят только лоты с чистой прибылью и ROI выше порога.",
        "cross_source": "Один и тот же тип монеты на двух площадках с разрывом цен ≥12%. Стратегия заработает, когда в базе появятся оба источника (SM + Мешок).",
        "slab_flip": "Монета в признанном слабе (NGC/PCGS/ННР) выставлена ниже медианы сопоставимых сделок — перепродажа без слабирования.",
        "underpriced": "Радар заниженных цен: ask ниже медианы рынка типа на ≥20% (по ≥3 comps). Не сделка «под ключ», а сигнал смотреть глазами.",
    }
    for strat in order:
        rs = by_strategy.get(strat, [])
        body = ""
        if rs:
            trs = []
            for r in rs[:10]:
                e = r["economics"]
                url = r.get("url")
                title = H.escape(r["coin"].title[:70])
                if url:
                    title = f'<a href="{H.escape(url)}" target="_blank" rel="noopener">{title}</a>'
                trs.append(
                    f'<tr><td>{title}<div class="mono" style="color:#6b7280">'
                    f'{H.escape(r["basis"][:90])}</div></td>'
                    f'<td class="num">{_fmt(r["buy_price"])}</td>'
                    f'<td class="num">{_fmt(r["expected_sale"])}</td>'
                    f'<td class="num pos">{_fmt(e.net_profit)}</td>'
                    f'<td class="num pos">{_pct(e.roi_pct)}</td>'
                    f'<td class="num">{r["confidence"]:.2f}</td>'
                    f'<td class="num">{r["score"]:,.0f}</td></tr>')
            body = ('<table><tr><th>Лот</th><th class="num">Вход</th>'
                    '<th class="num">Ожид. продажа</th><th class="num">Чистыми</th>'
                    '<th class="num">ROI</th><th class="num">Увер.</th>'
                    f'<th class="num">Score</th></tr>{"".join(trs)}</table>')
        else:
            body = ('<div class="note" style="margin:8px 0">Пусто. ' +
                    ("Порог прибыли/ROI не пройден ни одним лотом."
                     if strat != "cross_source" else
                     "Нужны лоты одного типа минимум с двух площадок: подключите Мешок "
                     "(<span class=\"mono\">harvest-meshok --storage-state cookies.json</span>).")
                    + '</div>')
        blocks.append(f'<h3>{H.escape(labels.get(strat, strat))} '
                      f'<span class="tag {"t-ok" if rs else "t-info"}">{len(rs)}</span></h3>'
                      f'<div class="sub" style="margin:2px 0 6px">{hints[strat]}</div>{body}')
    return "".join(blocks)


def _index_html(idx: list[dict]) -> str:
    if not idx:
        return '<div class="note">Индекс пуст — нет листингов с опознанными типами.</div>'
    trs = []
    for r in idx[:20]:
        prem = (f'<b class="{"pos" if r["slab_premium"] and r["slab_premium"] >= 2 else ""}">'
                f'{r["slab_premium"]}x</b>' if r["slab_premium"] else "—")
        grs = ", ".join(f"{g} {int(v):,}".replace(",", " ")
                        for g, v in list(r["graders"].items())[:3]) or "—"
        trs.append(
            f'<tr><td class="mono">{H.escape(r["comp_key"])}'
            f'<div style="color:#6b7280">{H.escape((r["titles"][0] if r["titles"] else "")[:56])}</div></td>'
            f'<td class="num">{r["n_listings"]}</td>'
            f'<td class="num">{r["n_raw"]}/{r["n_slab"]}</td>'
            f'<td class="num">{_fmt(r["raw_med"])}</td>'
            f'<td class="num">{_fmt(r["slab_med"])}</td>'
            f'<td class="num">{prem}</td>'
            f'<td class="num">{_fmt(r["deal_med"]) if r["n_deals"] else "—"}'
            f'<div class="mono" style="color:#6b7280">{r["n_deals"]} сделок</div></td>'
            f'<td class="num">{_pct(r["spread_pct"])}</td>'
            f'<td class="mono">{H.escape(grs)}</td></tr>')
    return ('<table><tr><th>Тип (год|номинал|металл)</th><th class="num">Лист.</th>'
            '<th class="num">Сыр/слаб</th><th class="num">Сыр. медиана</th>'
            '<th class="num">Слаб. медиана</th><th class="num">Премия слаба</th>'
            '<th class="num">Сделки</th><th class="num">Спред ask</th>'
            f'<th>Грейдеры</th></tr>{"".join(trs)}</table>')


def _deals_html(ctx: dict) -> str:
    by_method = ctx.get("deals_by_method") or []
    recent = ctx.get("deals_recent") or []
    if not by_method:
        return ('<div class="note warn">База сделок пуста. Способы наполнить: '
                '<span class="mono">harvest-meshok --sold</span> (завершённые лоты Мешка), '
                '<span class="mono">import-sold архив.csv|json|html</span> (архивы Конрос/Волмар/RareCoins), '
                'ежедневный <span class="mono">harvest-sm</span> (исчезнувшие лоты = прокси продаж).</div>')
    mrows = "".join(
        f'<tr><td>{H.escape(str(r["method"]))}</td><td>{H.escape(str(r["source"]))}</td>'
        f'<td class="num">{r["c"]}</td></tr>' for r in by_method)
    rrows = "".join(
        f'<tr><td>{H.escape((r["title"] or "")[:60])}<div class="mono" style="color:#6b7280">'
        f'{H.escape(r["comp_key"] or "—")}</div></td>'
        f'<td class="num">{_fmt(r["price_rub"])}</td>'
        f'<td>{H.escape(r["grader"] or "raw")} {r["grade_num"] or ""}</td>'
        f'<td>{_date(r["sold_at"])}</td><td>{H.escape(str(r["method"]))}</td></tr>'
        for r in recent)
    return (f'<div class="two"><div><h3>По способам</h3>'
            f'<table><tr><th>Метод</th><th>Источник</th><th class="num">Сделок</th></tr>{mrows}</table></div>'
            f'<div><h3>Последние сделки</h3>'
            f'<table><tr><th>Лот</th><th class="num">Цена</th><th>Грейд</th><th>Дата</th><th>Метод</th></tr>'
            f'{rrows}</table></div></div>')


def _calibration_html(ctx: dict) -> str:
    cal = ctx.get("calibration") or {}
    ngs = cal.get("ngs") or {}
    pairs = cal.get("pairs") or []
    terms_discount = ctx.get("terms", {}).get("ngs_discount")
    if ngs.get("n"):
        head = (f'<div class="note"><b>Скидка NGS измерена:</b> медиана {ngs["discount"]} '
                f'по {ngs["n"]} парам (min {ngs["min"]}, max {ngs["max"]}). '
                f'В допущениях сейчас {terms_discount}.</div>')
    else:
        head = (f'<div class="note crit"><b>Скидка NGS НЕ измерена.</b> В допущениях стоит '
                f'гипотеза <span class="mono">ngs_discount={terms_discount}</span>. Все суммы '
                f'«слаб → продажа» — сценарий, а не прогноз. Нужны пары «та же монета, тот же '
                f'грейд: NGS vs NGC/ННР» — они появятся, когда база сделок наполнится '
                f'NGS-слабами (Мешок, архивы аукционов).</div>')
    prows = "".join(
        f'<tr><td class="mono">{H.escape(p["comp_key"])}</td><td>{p["grade"]}</td>'
        f'<td>{H.escape(p["grader_a"])}</td><td class="num">{_fmt(p["price_a"])}</td>'
        f'<td>{H.escape(p["grader_b"])}</td><td class="num">{_fmt(p["price_b"])}</td>'
        f'<td class="num">{p["ratio"]}</td></tr>' for p in pairs[:12])
    table = (f'<h3>Найденные пары грейдеров</h3>'
             f'<table><tr><th>Тип</th><th>Грейд</th><th>Младший (A)</th><th class="num">Цена A</th>'
             f'<th>Эталон (B)</th><th class="num">Цена B</th><th class="num">Скидка A/B</th></tr>'
             f'{prows}</table>') if prows else ""
    return head + (table or '<div class="sub">Пар пока нет вовсе — в базе мало slab-цен одного типа.</div>')


def _flagship_html(ctx: dict) -> str:
    fe = ctx.get("flagship")
    if not fe:
        return ""
    c = fe["coin"]
    ec_rows = "".join([
        f'<tr><td>{fe["entry_label"]}</td><td class="num neg">−{_fmt(fe["buy"])}</td></tr>',
        f'<tr><td>Слабирование NGS ({fe["tariff"]})</td><td class="num neg">−{_fmt(fe["slab"])}</td></tr>',
        f'<tr><td>Логистика (до NGS и до покупателя)</td><td class="num neg">−{_fmt(fe["ship"])}</td></tr>',
        f'<tr><td>Продажа в слабе NGS</td><td class="num pos">+{_fmt(fe["sale"])}</td></tr>',
        f'<tr><td>Комиссия Мешка</td><td class="num neg">−{_fmt(fe["fee"])}</td></tr>',
        f'<tr><td>Резерв на риски ({fe["risk_pct"]:.0%})</td><td class="num neg">−{_fmt(fe["risk"])}</td></tr>',
        f'<tr style="border-top:2px solid #3b82f6"><td><b>Чистая прибыль с одной монеты</b></td>'
        f'<td class="num {"pos" if fe["net"] > 0 else "neg"}"><b>'
        f'{"+" if fe["net"] > 0 else "−"}{_fmt(abs(fe["net"]))}</b></td></tr>',
        f'<tr><td>ROI на вложенные деньги</td>'
        f'<td class="num {"pos" if fe["roi"] > 0 else "neg"}">{_pct(fe["roi"])}</td></tr>',
        f'<tr><td>Цена продажи, при которой выходим в ноль</td><td class="num">{_fmt(fe["breakeven"])}</td></tr>',
    ])
    comps_rows = "".join(
        f'<tr><td class="num">{_fmt(x.price)}</td><td>{H.escape(x.grader or "без слаба")}</td>'
        f'<td class="num">{x.grade_num or "raw"}</td><td>{x.origin}</td></tr>'
        for x in fe["comps"])
    return f"""
<h2>Юнит-экономика: {H.escape(c.title[:70])}{"." if not c.title.endswith(".") else ""}</h2>
<div class="two">
<div><h3>Сделка, ₽</h3><table>{ec_rows}</table></div>
<div><h3>Аналоги (comps), ₽</h3>
<table><tr><th>Цена</th><th>Грейдер</th><th>Грейд</th><th>Источник</th></tr>{comps_rows}</table>
<div class="note">{H.escape(fe["basis"])}</div></div>
</div>"""


def build(ctx: dict) -> str:
    """ctx: stats, watch, terms, tariffs, coverage, flagship, opportunities,
    by_strategy, index_top, calibration, deals_recent, deals_by_method."""
    st, watch, terms = ctx["stats"], ctx["watch"], ctx["terms"]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    kpi = [
        (st["lots"], "лотов в базе"), (st["coins"], "монет опознано"),
        (st["deals"], "сделок"), (st["comps"], "записей comps"),
        (st["index_types"], "типов в индексе"), (st["opportunities"], "возможностей"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>'
        for v, l in kpi)

    cov = ctx["coverage"]
    cov_rows = "".join(
        f'<tr><td>{H.escape(k)}</td><td class="num">{v["n"]} / {v["total"]}</td>'
        f'<td class="num">{v["pct"]:.0f}%</td>'
        f'<td><div class="bar"><i style="width:{v["pct"]:.0f}%"></i></div></td></tr>'
        for k, v in cov.items())
    cov_html = (f'<table><tr><th>Поле</th><th class="num">Заполнено</th>'
                f'<th class="num">Доля</th><th style="width:130px">Покрытие</th></tr>{cov_rows}</table>')

    tr = "".join(
        f'<tr><td>{n}</td><td class="num">{_fmt(p)}</td>'
        f'<td class="num">{"без ограничений" if cap == float("inf") else "до " + _fmt(cap)}</td>'
        f'<td class="num">{d} дн.</td></tr>'
        for n, p, cap, d in ctx["tariffs"])

    terms_rows = "".join(
        f'<tr><td class="mono">{H.escape(k)}</td><td class="num">{v}</td></tr>'
        for k, v in ctx["terms"].items())

    demo = ' <span class="tag t-warn">comps=DEMO</span>' if st.get("comps") and not st.get("deals") else ""
    demo_banner = ""
    if ctx.get("demo"):
        demo_banner = ('<div class="note crit" style="border-left-color:#a855f7">'
                       '<b>⚠ ДЕМО-РЕЖИМ.</b> Все лоты, сделки и цены в этом отчёте — '
                       'синтетика (seller/source начинаются с «DEMO-»), созданная командой '
                       '<span class="mono">scan.py demo</span>, чтобы показать работу всех '
                       'стратегий, калибровки и отчёта. К реальному рынку отношения не имеют.'
                       '</div>')

    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coinarb — аналитика арбитража монет{" (ДЕМО)" if ctx.get("demo") else ""}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>Арбитраж монет: полная картина{" — ДЕМО" if ctx.get("demo") else ""}</h1>
<div class="sub">staraya-moneta.ru · meshok.net · архивы аукционов → NGS → продажа ·
отчёт собран {now}{demo}</div>
{demo_banner}

<div class="grid">{kpi_html}</div>

<div class="note crit"><b>Главное, что показали данные.</b> На staraya-moneta почти всё уже
в слабах NGC/ННР и стоит по верхней планке рынка — вход через этот магазин невыгоден.
Деньги в схеме лежат там, где есть <b>сырые</b> монеты (Мешок, региональные продавцы) и
<b>факты продаж</b> (архивы аукционов). Поэтому ядро системы — база сделок: она наполняет
индекс, калибрует скидку NGS и включает стратегии cross_source / slab_flip.</div>

<h2>Возможности по стратегиям</h2>
{_opportunities_html(ctx.get("by_strategy", {}), ctx.get("strategy_labels", {}))}

{_flagship_html(ctx)}

<h2>Ценовой индекс по типам</h2>
<div class="sub">«Премия слаба» — во сколько раз медиана slab-листингов выше медианы сырых
того же типа. Это измеренный рынком приз схемы «сырая → слаб» (до вычета затрат на NGS).
«Сделки» — медиана фактических продаж из базы deals.</div>
{_index_html(ctx.get("index_top", []))}

<h2>Калибровка скидки NGS</h2>
{_calibration_html(ctx)}

<h2>База сделок</h2>
<div class="sub">Факты продаж: завершённые лоты Мешка, архивы аукционов, исчезнувшие
листинги (vanish-прокси), ручной импорт.</div>
{_deals_html(ctx)}

<h2>Лист наблюдения: лоты с аналогами</h2>
<div class="sub">Отсортировано по чистой прибыли. «Сырая» — не в признанном слабе, кандидат
на слабирование. Отрицательная прибыль означает, что вход по текущей цене невыгоден —
это тоже сигнал.</div>
<table><tr><th>Лот</th><th class="num">Вход</th><th class="num">Ожид. продажа</th>
<th class="num">Слаб</th><th class="num">Чистыми</th><th class="num">ROI</th><th>Уверенность</th></tr>
{_watch_rows(watch)}</table>

<h2>Покрытие данных</h2>
<div class="sub">Насколько полно удаётся восстановить признаки лота из заголовка продавца.
Это и есть предел аналитики: без номинала и года монету не с чем сравнивать.</div>
{cov_html}

<div class="two">
<div><h2>Тарифы NGS (грейдинг + слабирование)</h2>
<table><tr><th>Тариф</th><th class="num">Цена</th><th class="num">Стоимость монеты</th>
<th class="num">Срок</th></tr>{tr}</table>
<div class="note warn">Тариф «Стандарт» за 1 200 ₽ действует только для монет рыночной
стоимостью <b>до 50 000 ₽</b>. При более дорогой монете — «Классик» 1 500 ₽ и выше.</div></div>
<div><h2>Допущения сделки</h2>
<table><tr><th>Параметр</th><th class="num">Значение</th></tr>{terms_rows}</table>
<div class="note">Все допущения меняются без правки кода:
<span class="mono">python scan.py analyze --terms '{{"ngs_discount":0.6}}'</span></div></div>
</div>

<h2>Метод и что делать дальше</h2>
<div class="note"><b>1. Наполняйте базу сделок.</b> Каждый ежедневный прогон
<span class="mono">harvest-sm</span> помечает исчезнувшие лоты как продажи (vanish-прокси).
Завершённые лоты Мешка: <span class="mono">harvest-meshok --sold</span>. Архивы аукционов
(Конрос, Волмар, Редкие Монеты): сохраните страницу архива и загрузите
<span class="mono">import-sold файл.html --source conros</span>. Через месяц у вас будет
собственная история цен, которой нет у тех, кто смотрит руками.</div>
<div class="note warn"><b>2. Meshok закрыт Cloudflare.</b> Из дата-центра Turnstile не
проходится. Нужны cookies вашего браузера (<span class="mono">--storage-state cookies.json</span>)
или резидентный прокси (<span class="mono">--proxy</span>). Парсер от способа доступа не зависит.</div>
<div class="note crit"><b>3. Ключевой параметр — скидка NGS</b>
(<span class="mono">ngs_discount={ctx["terms"].get("ngs_discount", "0.7")}</span>). Пока она не
откалибрована по реальным парам цен, суммы в отчёте — сценарий, а не прогноз. Следите за
секцией «Калибровка скидки NGS» выше.</div>

<div class="foot">Сгенерировано автоматически · coinarb · данные: staraya-moneta.ru, meshok.net,
ngs.vc, архивы аукционов<br>Демо-значения comps помечены source=DEMO и не являются рыночными данными.</div>
</div></body></html>"""
