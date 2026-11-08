"""ДЕМО-режим: синтетический рынок в отдельной БД, чтобы увидеть систему в работе.

Зачем: реальные данные пока не собрать из песочницы (Мешок закрыт Cloudflare,
SM даёт только один срез витрины). Демо показывает, КАК поведут себя все четыре
стратегии, калибровка NGS и отчёт, когда база сделок наполнится. Все записи
помечены seller/source «DEMO-*» — перепутать с реальным рынком невозможно.

Сценарий намеренно жизненный:
  * витрина SM — дорогие слибы NGC/ННР (как в настоящем срезе);
  * Мешок — сырые монеты дешевле + завершённые аукционы (факты продаж);
  * архивы Конрос/Волмар — hammer-цены, в т.ч. NGS-слабы для калибровки;
  * один лот NGC-слаба на Мешке ниже рынка — срабатывает slab_flip;
  * один тип на двух площадках с разрывом — срабатывает cross_source.
"""
from __future__ import annotations
import time

from . import store
from .sources import sold as sold_src

DAY = 86400.0
NOW = time.time()


def _lot(con, source, sid, title, price, section="", seller="DEMO", days_ago=0.0):
    lot_id = store.upsert_lot(con, source=source, source_id=sid, url="",
                              title=title, price=price, section=section, seller=seller)
    if days_ago:                     # «состариваем» для осмысленной ликвидности
        con.execute("UPDATE lots SET first_seen=?, last_seen=? WHERE id=?",
                    (NOW - days_ago * DAY, NOW - days_ago * DAY * 0.5, lot_id))
    from .analyze import enrich
    from .normalize import parse
    store.save_features(con, lot_id, enrich(parse(title, section)))
    return lot_id


def _deal(con, title, price, source, method, days_ago, grader=None, url=""):
    rows = [{"title": title, "price_rub": price, "sold_at": NOW - days_ago * DAY,
             "source": source, "url": url}]
    sold_src.import_rows(con, rows, source, method)


def build(db_path: str):
    con = store.connect(db_path)

    # ---------------- витрина staraya-moneta (слибы по верхней планке) --------
    _lot(con, "staraya-moneta", "d1",
         "Рубль 1913 г. (ВС) 300 лет дома Романовых, выпуклый чекан, в слабе ННР MS 62",
         80000, "Царская Россия", "DEMO-SM", days_ago=30)
    _lot(con, "staraya-moneta", "d2",
         "10 рублей 1899 г. (АГ), золото, в слабе ННР MS62",
         48000, "Царская Россия", "DEMO-SM", days_ago=12)
    _lot(con, "staraya-moneta", "d3",
         "5 рублей 1898 г. АГ, золото, в слабе NGC MS62",
         52000, "Царская Россия", "DEMO-SM", days_ago=45)
    _lot(con, "staraya-moneta", "d4",
         "15 копеек 1903 СПБ АР, в слабе NGC MS64",
         9000, "Царская Россия", "DEMO-SM", days_ago=8)

    # ---------------- Мешок: сырые монеты и слибы ------------------------------
    _lot(con, "meshok", "m1",
         "Рубль 1913 300 лет дома Романовых, выпуклый чекан, XF",
         13000, "Россия до 1917", "DEMO-meshok", days_ago=3)
    _lot(con, "meshok", "m2",
         "10 рублей 1899 (АГ) золото в слабе ННР MS62",
         58000, "Россия до 1917", "DEMO-meshok", days_ago=5)
    _lot(con, "meshok", "m3",
         "5 рублей 1898 АГ золото в слабе NGC MS62, из коллекции",
         34000, "Россия до 1917", "DEMO-meshok", days_ago=2)
    _lot(con, "meshok", "m4",
         "15 копеек 1903 СПБ АР, сырая, штемпельный блеск",
         1400, "Россия до 1917", "DEMO-meshok", days_ago=1)
    _lot(con, "meshok", "m5",
         "20 копеек 1913 СПБ ВС в слабе NGS MS63",
         6200, "Россия до 1917", "DEMO-meshok", days_ago=6)

    # ---------------- факты продаж: завершённые аукционы Мешка ----------------
    _deal(con, "Рубль 1913 (ВС) 300 лет дома Романовых в слабе ННР MS62",
          44000, "DEMO-meshok-ended", "ended-listing", days_ago=10)
    _deal(con, "10 рублей 1899 (АГ) золото в слабе NGC MS62",
          51000, "DEMO-meshok-ended", "ended-listing", days_ago=14)
    _deal(con, "10 рублей 1899 (АГ) золото в слабе NGS MS62",
          34000, "DEMO-meshok-ended", "ended-listing", days_ago=14)
    _deal(con, "20 копеек 1913 СПБ ВС в слабе NGS MS63",
          5800, "DEMO-meshok-ended", "ended-listing", days_ago=9)
    _deal(con, "20 копеек 1913 СПБ ВС в слабе NGC MS63",
          9200, "DEMO-meshok-ended", "ended-listing", days_ago=9)

    # ---------------- архивы аукционных домов ----------------------------------
    _deal(con, "Рубль 1913 (ВС) Романовых выпуклый чекан, сырая XF",
          12500, "DEMO-conros", "auction-archive", days_ago=40)
    _deal(con, "Рубль 1913 (ВС) Романовых в слабе NGC MS62",
          61000, "DEMO-conros", "auction-archive", days_ago=40)
    _deal(con, "5 рублей 1898 АГ золото в слабе NGC MS62",
          53000, "DEMO-wolmar", "auction-archive", days_ago=25)
    _deal(con, "5 рублей 1898 АГ золото в слабе NGC MS62",
          55000, "DEMO-rarecoins", "auction-archive", days_ago=55)
    _deal(con, "5 рублей 1898 АГ золото, сырая AU",
          33000, "DEMO-conros", "auction-archive", days_ago=25)
    _deal(con, "15 копеек 1903 СПБ АР, сырая VF",
          1100, "DEMO-wolmar", "auction-archive", days_ago=18)
    _deal(con, "15 копеек 1903 СПБ АР в слабе NGC MS64",
          8200, "DEMO-rarecoins", "auction-archive", days_ago=18)
    return con
