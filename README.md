# `data` 分支 —— 只放資料，不放程式

這個分支是 [youbike-forecast](https://github.com/Bryant0909/youbike-forecast) 的**資料倉庫**。
它是一個**孤兒分支**（orphan branch）：跟 `main` 沒有任何共同的歷史，
所以資料的 commit 紀錄不會污染 `main` 的歷史。

程式碼請看 [`main` 分支](https://github.com/Bryant0909/youbike-forecast/tree/main)。

## 內容

```
stations.parquet          站點基本資料（站名、行政區、地址、經緯度）—— 幾乎不變
snapshots/
├── 2026-09-30/           今天還沒壓縮的 5 分鐘小檔
│   ├── 20260930T090500.parquet
│   └── ...
└── 2026-09-29.parquet    已壓縮的整日檔（phase 0-5 每日凌晨產生）
```

小檔的檔名是**資料的來源時間**（台北時間），不是抓取時間。
這樣同一份來源資料不管被抓幾次都只會存一份，天然去重。

## 怎麼取用

```bash
git clone https://github.com/Bryant0909/youbike-forecast.git
cd youbike-forecast
git worktree add data-branch data
```

詳細說明看 `main` 分支的 [`SETUP.md`](https://github.com/Bryant0909/youbike-forecast/blob/main/SETUP.md)。

## 欄位

`snapshots/*.parquet`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `station_id` | string | 站點編號 |
| `is_active` | int8 | 1=營運中, 0=停用 |
| `total_docks` | int16 | 總車柱數 |
| `bikes_available` | int16 | 可借車輛數（**預測目標**） |
| `docks_available` | int16 | 可還空位數 |
| `src_time` | timestamp UTC | 資料本身的產生時間（來源給的） |
| `fetched_at` | timestamp UTC | 我們發出請求的時間 |

> ⚠️ 這個分支由 GitHub Actions 自動寫入，**不要手動修改**。
