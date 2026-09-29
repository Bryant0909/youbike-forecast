"""
phase 0-1：確認臺北市 YouBike 2.0 即時資料 API 可用。

這支程式「只讀不寫」，目的是在寫正式的收集程式之前，先親眼確認：
  1. API 到底通不通、回傳什麼格式
  2. 一共有幾個站點
  3. 每個站點有哪些欄位、欄位名稱長什麼樣（文件常常跟實際不一樣，所以要親眼看）
  4. 資料有沒有「來源更新時間」—— 這很重要，因為 API 可能回傳的是幾分鐘前的舊資料，
     我們必須同時記錄「我抓的時間」和「資料本身的時間」才能判斷資料新不新
"""

import json
from datetime import datetime, timezone, timedelta

import requests

# 台北時間 = UTC+8。之後所有時間都會同時存 UTC 和台北時間，避免時區搞混
TAIPEI_TZ = timezone(timedelta(hours=8))

# 可能的資料來源，依可靠度排序，一個一個試
# 1) tcgbusfs 這個網址是臺北市交通局實際放檔案的地方，data.taipei 上的資料集也是指向這裡。
#    它是單純的靜態 JSON 檔，速度快、不用申請 key、也沒有分頁問題 —— 最適合每 5 分鐘抓一次
# 2) data.taipei 的 API 是官方入口，但有分頁（一次最多 1000 筆）、而且比較慢
CANDIDATES = [
    (
        "tcgbusfs 直連 JSON",
        "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json",
    ),
    (
        "data.taipei API",
        "https://data.taipei/api/v1/dataset/8ef1626a-892a-4218-8344-f7ac46e1aa48"
        "?scope=resourceAquire&limit=5",
    ),
]


def try_one(name: str, url: str):
    """嘗試抓一個網址，回傳 (是否成功, 解析後的資料)。"""
    print(f"\n{'=' * 70}")
    print(f"嘗試：{name}")
    print(f"網址：{url[:90]}{'...' if len(url) > 90 else ''}")
    print("=" * 70)

    try:
        # timeout 一定要設，不然網路卡住時程式會一直掛著（之後在 Actions 上會浪費額度）
        resp = requests.get(url, timeout=20)
    except Exception as e:
        print(f"[失敗] 連線出錯：{type(e).__name__}: {e}")
        return False, None

    print(f"HTTP 狀態碼：{resp.status_code}")
    print(f"回應大小：{len(resp.content) / 1024:.1f} KB")
    print(f"Content-Type：{resp.headers.get('Content-Type', '(無)')}")

    if resp.status_code != 200:
        print("[失敗] 狀態碼不是 200")
        return False, None

    try:
        data = resp.json()
    except Exception as e:
        print(f"[失敗] 不是合法的 JSON：{e}")
        print(f"開頭 200 字：{resp.text[:200]}")
        return False, None

    print(f"[成功] JSON 解析成功，最外層型別：{type(data).__name__}")
    return True, data


def extract_records(data):
    """
    把不同來源的回傳格式統一成「站點列表」。

    tcgbusfs 直接回傳一個 list；
    data.taipei 則包了好幾層：{"result": {"results": [...]}}。
    """
    if isinstance(data, list):
        return data, "最外層就是 list"
    if isinstance(data, dict):
        # data.taipei 的格式
        if "result" in data and isinstance(data["result"], dict):
            res = data["result"]
            if "results" in res:
                return res["results"], "data['result']['results']"
        # 有些來源是 {"retVal": {...}} 這種用站點 ID 當 key 的字典
        for key in ("retVal", "data", "records"):
            if key in data:
                v = data[key]
                if isinstance(v, list):
                    return v, f"data['{key}']"
                if isinstance(v, dict):
                    return list(v.values()), f"data['{key}'] 的 values"
    return None, "認不出來的格式"


def main():
    now_local = datetime.now(TAIPEI_TZ)
    print(f"現在時間（台北）：{now_local:%Y-%m-%d %H:%M:%S}")

    for name, url in CANDIDATES:
        ok, data = try_one(name, url)
        if not ok:
            continue

        records, how = extract_records(data)
        if not records:
            print(f"[失敗] 找不到站點列表（{how}）")
            continue

        print(f"\n站點列表位置：{how}")
        print(f"站點數量：{len(records)} 筆")

        first = records[0]
        if not isinstance(first, dict):
            print(f"[失敗] 每一筆不是字典，而是 {type(first).__name__}")
            continue

        # ---- 印出一筆完整範例 ----
        print(f"\n{'-' * 70}")
        print("一筆完整範例資料（原始 JSON）：")
        print("-" * 70)
        print(json.dumps(first, ensure_ascii=False, indent=2))

        # ---- 列出所有欄位：名稱、型別、範例值 ----
        print(f"\n{'-' * 70}")
        print(f"全部欄位一覽（共 {len(first)} 個）：")
        print("-" * 70)
        print(f"{'欄位名稱':<22} {'型別':<8} 範例值")
        print("-" * 70)
        for k, v in first.items():
            sample = str(v)
            if len(sample) > 34:
                sample = sample[:31] + "..."
            print(f"{k:<22} {type(v).__name__:<8} {sample}")

        # ---- 檢查有沒有「行政區」欄位，以及大安區有幾站 ----
        print(f"\n{'-' * 70}")
        print("重點檢查：")
        print("-" * 70)
        area_keys = [k for k in first if k.lower() in ("sarea", "area", "sareaen", "district")]
        if area_keys:
            ak = area_keys[0]
            areas = {}
            for r in records:
                areas[r.get(ak)] = areas.get(r.get(ak), 0) + 1
            print(f"行政區欄位 = '{ak}'，共 {len(areas)} 區")
            top = sorted(areas.items(), key=lambda x: -x[1])[:5]
            print(f"  站點最多的 5 區：{top}")
            daan = [v for k, v in areas.items() if k and "大安" in str(k)]
            print(f"  大安區站點數：{daan[0] if daan else '找不到大安區'}")
        else:
            print("找不到行政區欄位（之後可能要用經緯度自己判斷）")

        # ---- 檢查資料新舊：來源更新時間 vs 現在時間 ----
        time_keys = [
            k for k in first
            if any(t in k.lower() for t in ("time", "mday", "updat"))
        ]
        print(f"時間相關欄位：{time_keys if time_keys else '無'}")
        for tk in time_keys:
            print(f"  {tk} = {first[tk]}")

        # ---- 檢查可借 / 可還的欄位 ----
        num_keys = [k for k, v in first.items() if isinstance(v, (int, float))]
        print(f"數值欄位（可借/可還/容量可能在這裡）：{num_keys}")

        print(f"\n{'=' * 70}")
        print(f"[結論] '{name}' 可以用！共 {len(records)} 站。")
        print("=" * 70)
        return 0

    print("\n[結論] 所有來源都失敗了，需要人工到 data.taipei 網站上查最新網址。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
