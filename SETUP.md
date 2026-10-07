# 換電腦 / 重新安裝：從零把專案跑起來

> 這份文件的用途：假設你手上是一台**全新的電腦**，只知道這個 repo 的網址，
> 照著做就能把整個專案（程式 + 累積的資料）完整還原。
>
> 專案的設計原則是 **「repo 就是唯一的真相來源」**：
> 只要 GitHub 上的 repo 還在，你的電腦壞掉、重灌、換新都不會丟任何東西。

---

## 0. 先確認一件事：什麼東西「不在」repo 裡

整個專案只有**一樣東西**不在 GitHub 上，換電腦時必須自己重建：

| 東西 | 為什麼不在 repo | 怎麼還原 |
|---|---|---|
| `.env`（API 金鑰） | 金鑰一旦 commit 就等於公開，絕對不能上傳 | 複製 `.env.example` 成 `.env`，再去各平台補金鑰（見第 4 步） |

其他全部（程式、文件、決策紀錄、**收集到的所有資料**）都在 repo 裡，`git clone` 一次就拿得到。

---

## 1. 安裝三樣工具

| 工具 | 版本 | 下載 |
|---|---|---|
| Git | 任何版本 | https://git-scm.com/downloads |
| Python | **3.11 以上** | https://www.python.org/downloads/ |
| GitHub CLI（`gh`） | 任何版本 | https://cli.github.com/ |

> 💡 安裝 Python 時記得勾選 **「Add Python to PATH」**，不然終端機會找不到 `python` 指令。

裝完確認一下：

```bash
git --version && python --version && gh --version
```

---

## 2. 登入 GitHub

```bash
gh auth login -s workflow
```

照著選 `GitHub.com` → `HTTPS` → `Login with a web browser`，
然後**一定要把整個流程走完**（複製一次性代碼 → 開 https://github.com/login/device → 貼上 → Authorize），
直到終端機印出 `✓ Authentication complete` 為止。

> ⚠️ `-s workflow` 這個參數不能省。沒有它的話，你之後修改
> `.github/workflows/` 底下的檔案會推不上去，GitHub 會直接拒絕。

確認權限拿到了（`Token scopes` 那行要看得到 `workflow`）：

```bash
gh auth status
```

---

## 3. 下載專案

```bash
git clone https://github.com/Bryant0909/youbike-forecast.git
```

> 📁 **建議不要放在 OneDrive / Google Drive / Dropbox 同步的資料夾裡**，
> 原因見最後一節「已知地雷」。放在像 `C:\dev\` 這種單純的本機資料夾就好。

---

## 4. 建立 `.env`（API 金鑰）

```bash
cd youbike-forecast
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

Mac / Linux：

```bash
cp .env.example .env
```

然後用文字編輯器打開 `.env`，把金鑰填進去。目前需要的：

- **`CWA_API_KEY`** — 中央氣象署開放資料平臺
  申請：https://opendata.cwa.gov.tw/ → 註冊 → 會員資訊 → API 授權碼

> 如果只是要跑資料收集（phase 0），**`.env` 可以先留空**，
> 收集 YouBike 資料不需要任何金鑰。天氣資料才需要。

---

## 5. 安裝 Python 套件

建議用虛擬環境（venv），這樣套件裝在專案資料夾裡，不會跟你電腦上其他 Python 專案打架：

```bash
python -m venv .venv
```

啟用它 —— Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Mac / Linux：

```bash
source .venv/bin/activate
```

啟用成功的話，終端機提示符前面會多出 `(.venv)`。然後裝套件：

```bash
pip install -r requirements.txt
```

然後把專案本身也裝起來（**只要做一次**）：

```bash
pip install -e .
```

這一步讓 `import youbike` 在任何地方都能用（notebook、scripts、測試）。
`-e` 是「可編輯安裝」，它只在 Python 的搜尋路徑裡登記 `src/` 的位置，不會複製程式碼 ——
所以你改了 `src/youbike/*.py` 之後不用重裝，馬上生效。

> 💡 沒做這一步的話，notebook 裡 `import youbike` 會說找不到模組。

> 之後每次要工作，都要先啟用 venv。忘記啟用的話會出現「找不到模組」的錯誤。

---

## 6. 確認能跑

```bash
python scripts/collect.py --dry-run
```

`--dry-run` 是「只抓資料、不寫檔」的測試模式。看到類似這樣就對了：

```
[18:00:29] 拿到 1808 站
[18:00:29] 資料產生時間：2026-09-29 17:59:17（台北），比現在舊 70 秒
[18:00:29] [dry-run] 不寫檔。表格大小：1808 列 x 7 欄
[18:00:30] 現況：營運中 1781 站，借不到車 108 站，還不了車 48 站
```

到這裡，**程式部分已經完全還原了**。

---

## 7. 取回累積的資料

所有收集到的資料存在同一個 repo 的 **`data` 分支**（不是 `main`）。
第 3 步的 `git clone` 其實已經把資料一起下載下來了，只是還沒「展開」。

用 `git worktree` 把它展開到 `data-branch/` 資料夾（`main` 的 `.gitignore` 已經忽略這個資料夾）：

```bash
git worktree add data-branch data
```

之後想更新到最新資料：

```bash
git -C data-branch fetch origin data
```

```bash
git -C data-branch reset --hard origin/data
```

> 為什麼不用 `git pull`？data 分支每月會「瘦身」一次（丟掉 git 歷史、只留目前的檔案，
> 見 `.github/workflows/backup.yml`）。瘦身後新舊歷史接不起來，`pull` 會失敗。
> `reset --hard` 是「直接變成遠端那個版本」，不管歷史怎麼變都能用。
> （`data-branch/` 裡不要放自己的東西 —— 這個指令會把它還原成遠端的樣子。）

### 舊月份的備份

每個月的資料也備份在 [GitHub Release](https://github.com/Bryant0909/youbike-forecast/releases)
（tag 叫 `data-YYYY-MM`）。萬一 data 分支出事，下載對應月份的 `data-YYYY-MM.tar.gz`
解壓到 `data-branch/` 就能還原；壓縮檔裡的 `MANIFEST.md` 列出每個檔的 SHA256 可以核對。

資料的結構長這樣：

```
data-branch/
├── stations.parquet              站點基本資料（站名、地址、經緯度）
└── snapshots/
    ├── 2026-09-29/               還沒壓縮的當日小檔
    │   ├── 20260929T174527.parquet
    │   └── ...
    └── 2026-09-28.parquet        已壓縮的整日檔
```

---

## 已知地雷

### ⚠️ pandas 3.x 在 Windows 可能被系統擋掉

症狀：`import pandas` 時出現

```
ImportError: DLL load failed while importing groupby: 應用程式控制原則已封鎖此檔案。
```

原因是 Windows 11 的「智慧型應用程式控制」對太新、還沒有信譽紀錄的 DLL 會直接封鎖。
`requirements.txt` 已經把 pandas 鎖在 `>=2.2,<3` 避開這件事，**不要自己升級到 3.x**。
如果真的中獎了：

```bash
pip install --force-reinstall --no-cache-dir "pandas==2.2.3"
```

### ⚠️ 不要把專案放在 OneDrive / 雲端同步資料夾裡

三個問題：

1. **會弄壞 git**。OneDrive 可能在 git 正在寫 `.git/` 的時候去同步那些檔案，
   造成 repo 損毀，而且錯誤訊息通常很難懂。
2. **會產生衝突複本**。你會看到 `collect (你的電腦的衝突複本).py` 這種檔案，
   很容易不小心改到錯的那份。
3. **檔案數量會爆掉**。資料累積起來是好幾萬個小檔，OneDrive 同步這些會非常慢，
   也會吃掉你的雲端空間 —— 而這完全沒必要，因為資料已經在 GitHub 上了。

**你不需要用雲端同步來備份這個專案** —— GitHub 就是備份。

### ⚠️ Python 找不到指令

如果 `python` 打下去說找不到，通常是安裝時沒勾「Add Python to PATH」。
Windows 上可以改用 `py` 代替 `python` 試試看，或是重新安裝並勾選那個選項。

---

## 一分鐘版本（給已經熟悉的你）

```bash
gh auth login -s workflow
git clone https://github.com/Bryant0909/youbike-forecast.git
cd youbike-forecast
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
cp .env.example .env          # 再自己填金鑰
python scripts/collect.py --dry-run
git worktree add data-branch data    # 展開累積的資料
```
