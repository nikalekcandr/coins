"""Генерация автономного HTML-отчёта (инлайн-стили, без внешних ресурсов)."""
from __future__ import annotations
import html as H
import statistics
from datetime import datetime

def _fmt(v, unit="₽"):
    if v is None: return "—"
    return f"{v:,.0f}".replace(",", " ") + (f" {unit}" if unit else "")

def _pct(v): return "—" if v is None else f"{v:.1f}%"

CSS = """
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#e6e8ee;
font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.4px}
h2{font-size:19px;margin:38px 0 12px;padding-bottom:8px;border-bottom:1px solid #262b36}
h3{font-size:15px;margin:22px 0 8px;color:#aab2c5}
.sub{color:#8b93a7;font-size:13px;margin-bottom:26px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
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

def build(ctx: dict) -> str:
    """ctx: stats, watch, terms, tariffs, coverage, opportunities"""
    st, watch, terms = ctx["stats"], ctx["watch"], ctx["terms"]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    kpi = [
        (st["lots"], "лотов в базе"), (st["coins"], "монет опознано"),
        (st["with_comps"], "с аналогами"), (st["comps"], "записей comps"),
        (st["opportunities"], "прошло фильтр"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>'
        for v, l in kpi)

    # --- лист наблюдения ---
    rows = []
    for r in watch[:24]:
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
<td>{badge}<div class="bar"><i style="width:{min(100, int(r['confidence']*100))}%"></i></div></td>
</tr>""")
    watch_html = "".join(rows) or '<tr><td colspan="7" style="color:#8b93a7">Нет лотов с аналогами. Загрузите comps: <span class="mono">python scan.py import-comps data/comps_seed.csv</span></td></tr>'

    # --- юнит-экономика флагмана ---
    fe = ctx.get("flagship")
    flag_html = ""
    if fe:
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
        flag_html = f"""
<h2>Юнит-экономика: {H.escape(c.title[:70])}{"" if c.title.endswith(".") else "."}</h2>
<div class="two">
<div><h3>Сделка, ₽</h3><table>{ec_rows}</table></div>
<div><h3>Аналоги (comps), ₽</h3>
<table><tr><th>Цена</th><th>Грейдер</th><th>Грейд</th><th>Источник</th></tr>{comps_rows}</table>
<div class="note">{H.escape(fe["basis"])}</div></div>
</div>"""

    # --- покрытие данных ---
    cov = ctx["coverage"]
    cov_rows = "".join(
        f'<tr><td>{H.escape(k)}</td><td class="num">{v["n"]} / {v["total"]}</td>'
        f'<td class="num">{v["pct"]:.0f}%</td>'
        f'<td><div class="bar"><i style="width:{v["pct"]:.0f}%"></i></div></td></tr>'
        for k, v in cov.items())
    cov_html = f'<table><tr><th>Поле</th><th class="num">Заполнено</th>' \
               f'<th class="num">Доля</th><th style="width:130px">Покрытие</th></tr>{cov_rows}</table>'

    # --- тарифы NGS ---
    tr = "".join(
        f'<tr><td>{n}</td><td class="num">{_fmt(p)}</td>'
        f'<td class="num">{"без ограничений" if cap == float("inf") else "до " + _fmt(cap)}</td>'
        f'<td class="num">{d} дн.</td></tr>'
        for n, p, cap, d in ctx["tariffs"])

    terms_rows = "".join(
        f'<tr><td class="mono">{H.escape(k)}</td><td class="num">{v}</td></tr>'
        for k, v in ctx["terms"].items())

    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coinarb — аналитика арбитража монет</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Арбитраж «сырая монета → слаб»</h1>
<div class="sub">staraya-moneta.ru → NGS → meshok.net · отчёт собран {now}</div>

<div class="grid">{kpi_html}</div>

<div class="note crit"><b>Главное, что показали данные.</b> На staraya-moneta почти всё уже
в слабах NGC/ННР и стоит по верхней планке рынка — там арбитража нет (0 лотов прошли фильтр).
Вход экономически осмыслен только на <b>сырых</b> монетах. При этом «повышение грейда» само
по себе денег не приносит: NGS котируется ниже NGC/ННР. Прибыль даёт разрыв между ценой
сырой монеты и ценой той же монеты в слабе.</div>

{flag_html}

<h2>Лист наблюдения: лоты с аналогами</h2>
<div class="sub">Отсортировано по чистой прибыли. «Сырая» — не в признанном слабе, кандидат на слабирование.
Отрицательная прибыль означает, что вход по текущей цене невыгоден — это тоже сигнал.</div>
<table><tr><th>Лот</th><th class="num">Вход</th><th class="num">Ожид. продажа</th>
<th class="num">Слаб</th><th class="num">Чистыми</th><th class="num">ROI</th><th>Уверенность</th></tr>
{watch_html}</table>

<h2>Покрытие данных</h2>
<div class="sub">Насколько полно удаётся восстановить признаки лота из заголовка продавца.
Это и есть предел аналитики: без номинала и года монету не с чем сравнивать.</div>
{cov_html}

<div class="two">
<div><h2>Тарифы NGS (грейдинг + слабирование)</h2>
<table><tr><th>Тариф</th><th class="num">Цена</th><th class="num">Стоимость монеты</th>
<th class="num">Срок</th></tr>{tr}</table>
<div class="note warn">Тариф «Стандарт» за 1 200 ₽ действует только для монет рыночной
стоимостью <b>до 50 000 ₽</b>. При более дорогой монете — «Классик» 1 500 ₽ и выше. Это важно:
если вы покупаете монету за 30 000 ₽ и продаёте за 80 000 ₽, тариф считается по стоимости монеты.</div></div>
<div><h2>Допущения сделки</h2>
<table><tr><th>Параметр</th><th class="num">Значение</th></tr>{terms_rows}</table>
<div class="note">Все допущения меняются без правки кода:
<span class="mono">python scan.py analyze --terms '{{"ngs_discount":0.6}}'</span></div></div>
</div>

<h2>Метод и что нужно сделать дальше</h2>
<div class="note"><b>1. Comps — это всё.</b> С одного сайта сопоставимых цен не собрать: по каждому
типу там один лот. Нужна база проданных цен — она наполняется через
<span class="mono">data/comps_seed.csv</span> (сейчас там демо-данные) либо автоматически:
лоты, которые исчезли из выдачи между прогонами, помечаются неактивными — это прокси «продано».
Запускайте <span class="mono">scan.py harvest-sm</span> ежедневно, и база начнёт набирать
реальную историю, которой ни у кого из «ручных» конкурентов нет.</div>
<div class="note warn"><b>2. Meshok закрыт Cloudflare.</b> Из дата-центра Turnstile не проходится
(проверено на Chromium, Firefox, curl_cffi). Нужны ваши cookies браузера
(<span class="mono">--storage-state</span>) или резидентный прокси. Парсер Мешка готов и
не зависит от способа доступа.</div>
<div class="note crit"><b>3. Ключевой параметр — скидка NGS.</b> По умолчанию стоит
{ctx["terms"].get("ngs_discount", "0.7")}. Его нельзя угадывать: измеряется по реальным парам
«та же монета в NGS vs в NGC/ННР» (функция <span class="mono">calibrate_ngs_discount</span>).
Пока он не откалиброван, все суммы — гипотеза.</div>

<div class="foot">Сгенерировано автоматически · coinarb · данные: staraya-moneta.ru, ngs.vc, kb.meshok.net<br>
Демо-значения comps помечены source=DEMO и не являются рыночными данными.</div>
</div></body></html>"""
