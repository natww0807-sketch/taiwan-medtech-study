"""Transcriptions checked against rendered official exam pages, 2026-10-14.

Image-only choices keep their A-D labels; the original page image is the
choice content and must remain attached to the card.
"""

from __future__ import annotations


MANUAL_OVERRIDES = {
    "113-1-血液血庫-19": {
        "stem": "有關健康成人血液常規檢驗的敘述，下列何者正確？①白血球分型中，通常占最高比例的是嗜中性球。②白血球分型中，通常占最低比例的是嗜酸性球。③MCV 是指每個 RBC 的平均體積。④MCH 是指每個 RBC 內所含血色素的平均濃度。⑤MCHC 是指每單位 RBC 體積內所含血色素的平均濃度。",
        "options": {"A": "②③④", "B": "①②④", "C": "①③⑤", "D": "②④⑤"},
        "warning": "已依原卷人工校對；可開啟原卷影像核對",
    },
    "113-1-血液血庫-51": {
        "stem": "有關 Lupus anticoagulant 相關疾病及後天性 A 型血友病（acquired hemophilia A），兩者之病因均與抗體有關。下列比較何者錯誤？①相關臨床症狀：前者為栓塞症狀、流產；後者為出血症狀。②抗體之特性：前者為自體抗體；後者為異體抗體。③抗體所對應之抗原：前者為磷脂質；後者為凝血因子。④APTT 檢測：兩者均延長；加入 platelet lysate 後，前者可以矯正，後者無法矯正。",
        "options": {"A": "①", "B": "②", "C": "③", "D": "④"},
        "warning": "已依原卷人工校對表格；可開啟原卷影像核對",
    },
    "113-1-血液血庫-57": {
        "stem": "下列何者為 APTT 試驗所需的試劑？①活化劑，例如 celite。②phospholipid。③tissue factor。④thrombin。⑤鈣離子。",
        "options": {"A": "①②⑤", "B": "①③④", "C": "②③⑤", "D": "①④⑤"},
        "warning": "已依原卷人工校對；可開啟原卷影像核對",
    },
}

# Some questions continue onto a page whose extracted text contains only the
# figure and choices. Keep those continuation pages attached to the question
# card even when the spreadsheet image flag is missing.
IMAGE_PAGE_OVERRIDES = {
    "113-2-生理病理-29": [7],
}

for question_id in (
    "115-2-血液血庫-16",
    "113-1-分子鏡檢-59",
    "111-1-微生物-69",
    "114-1-臨床生化-06",
    "114-2-免疫病毒-42",
):
    MANUAL_OVERRIDES[question_id] = {
        "options": {letter: f"原卷圖像選項 {letter}（請看上方原卷影像）" for letter in "ABCD"},
        "warning": "已核對原卷圖像選項；請依原卷影像作答",
    }
