# `data` 分支 —— 只放資料，不放程式

這個分支是 [youbike-forecast](https://github.com/Bryant0909/youbike-forecast) 的**資料倉庫**。
它是一個**孤兒分支**（orphan branch）：跟 `main` 沒有任何共同的歷史，
所以資料的 commit 紀錄不會污染 `main` 的歷史。

程式碼請看 [`main` 分支](https://github.com/Bryant0909/youbike-forecast/tree/main)。

## 內容

```
stations.parquet          YouBike 站點基本資料（站名、行政區、地址、經緯度）—— 幾乎不變
snapshots/                YouBike 即時資料（每 5 分鐘）
├── 2026-09-30/           今天還沒壓縮的小檔
│   ├── 20260930T090500.parquet
│   └── ...
└── 2026-09-29.parquet    已壓縮的整日檔（每日凌晨產生）

weather_stations.parquet  氣象測站基本資料（站名、縣市、鄉鎮、經緯度、海拔）
weather/                  中央氣象署自動氣象站觀測（每 10 分鐘）
└── 2026-09-30/
    └── 20260930T174000.parquet
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

`weather/*.parquet`（來源：中央氣象署 O-A0003-001 自動氣象站）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `station_id` | string | 氣象測站編號 |
| `temp_c` | float32 | 氣溫（°C） |
| `humidity_pct` | float32 | 相對濕度（%） |
| `pressure_hpa` | float32 | 氣壓（hPa） |
| `wind_speed_ms` | float32 | 風速（m/s） |
| `wind_dir_deg` | float32 | 風向（度，0=北） |
| `gust_ms` | float32 | 陣風最大風速（m/s） |
| `precip_mm` | float32 | 雨量（mm）⚠️ 語意待確認，見下 |
| `uv_index` | float32 | 紫外線指數 |
| `sunshine` | float32 | 日照 |
| `weather` | string | 天氣現象文字（晴／多雲／多雲有雨…） |
| `obs_time` | timestamp UTC | 觀測時間（來源給的） |
| `fetched_at` | timestamp UTC | 我們發出請求的時間 |

> 💡 氣象署用 `-99` 表示缺值（連文字欄位也是），收集時已經全部轉成 **null**。
> 所以空值代表「儀器沒有這個資料」，不是 0。
>
> ⚠️ `precip_mm` 究竟是「本日累積」「本小時累積」還是「這 10 分鐘的量」
> 尚未確認，**在驗證之前不要拿它做特徵**。

> ⚠️ 這個分支由 GitHub Actions 自動寫入，**不要手動修改**。
