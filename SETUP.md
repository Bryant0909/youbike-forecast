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
| Python | **3.11**（跟 GitHub Actions 一樣；**不要用 3.13 以上**，見「已知地雷」） | https://www.python.org/downloads/ |
| GitHub CLI（`gh`） | 任何版本 | https://cli.github.com/ |

> 💡 Windows 安裝 Python 時記得勾選 **「Add Python to PATH」**，不然終端機會找不到 `python` 指令。

**Mac**：建議全部用 [Homebrew](https://brew.sh/) 裝。Mac 本身內建的 Python 版本不一定是 3.11，
所以要明確指定裝 `python@3.11`，之後也用 `python3.11` 這個指令（不是 `python` 或 `python3`）：

```bash
brew install git gh python@3.11
```

裝完確認一下：

```bash
git --version && python --version && gh --version
```

Mac：

```bash
git --version && python3.11 --version && gh --version
```

### 設定 git 的作者名字（新電腦一定要做）

新電腦的 git 不知道你是誰，commit 會用電腦的使用者名稱，GitHub 上就對不到你的帳號。
設定一次就好（`--global` 是整台電腦通用）：

```bash
git config --global user.name "Bryant Lin"
```

```bash
git config --global user.email "你的 GitHub 帳號 email"
```

> email 要跟 GitHub 帳號登記的一樣（或用 GitHub 設定頁給的 `...@users.noreply.github.com`），
> commit 才會連到你的頭像和貢獻紀錄。設好後用 `git config user.name` 確認印出來的是 `Bryant Lin`。

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

> ⚠️ **Mac 請一定要用上面的 `cp` 指令建立，不要在 Finder 裡複製改名。**
> Finder 不允許「.」開頭的檔名，存出來會變成 `env`（沒有點）——
> 程式讀不到，而且 `env` **不在 `.gitignore` 裡**，一不小心就會把金鑰 commit 上去。
> 如果已經變成 `env` 了，用終端機改回來：`mv env .env`

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

Mac（明確用 3.11 建，這樣 `.venv` 裡的 Python 就跟 Actions 同一版）：

```bash
python3.11 -m venv .venv
```

啟用它 —— Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

Mac / Linux：

```bash
source .venv/bin/activate
```

啟用成功的話，終端機提示符前面會多出 `(.venv)`（之後在 venv 裡打 `python` 就是 3.11，Mac 也一樣）。然後裝套件：

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

Mac 上 `python` 這個指令本來就可能不存在，請用 `python3.11`（或先啟用 venv，啟用後 `python` 就有了）。

### ⚠️ Python 3.13 以上連不到氣象署（天氣資料抓不到）

症狀：跑 `scripts/collect_weather.py` 時出現

```
SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Missing Subject Key Identifier
```

原因：Python 3.13 起預設開啟更嚴格的憑證檢查（`VERIFY_X509_STRICT`），
而氣象署網站的憑證少了一個欄位（Subject Key Identifier），在嚴格模式下會被判定不合格。
這不是你的電腦或網路的問題，是新版 Python 比較挑剔。

解法：**用 Python 3.11 建 venv**（第 5 步），跟 GitHub Actions 用的版本一樣。
Mac 上最容易中獎 —— Homebrew 的 `python3` 通常是最新版（3.13、3.14），
所以要明確打 `python3.11 -m venv .venv`。已經用錯版本建好的話：

```bash
rm -rf .venv && python3.11 -m venv .venv
```

然後重新做第 5 步的 `pip install`。

### ⚠️ Mac 的 Finder 會把 `.env` 存成 `env`

Finder（包括「另存新檔」對話框）不讓你用「.」開頭的檔名，會自動拿掉那個點。
結果程式找不到 `.env`，而且 `env` 這個名字**沒有被 `.gitignore` 擋掉**，
`git add .` 時金鑰就會被一起 commit。建立或改名 `.env` 一律用終端機的 `cp` / `mv`（見第 4 步）。

> 💡 在 Finder 裡按 `Cmd + Shift + .` 可以顯示隱藏檔，確認 `.env` 有沒有在。

---

## 一分鐘版本（給已經熟悉的你）

```bash
git config --global user.name "Bryant Lin"   # 再設 user.email
gh auth login -s workflow
git clone https://github.com/Bryant0909/youbike-forecast.git
cd youbike-forecast
python -m venv .venv && .\.venv\Scripts\Activate.ps1      # Mac：python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env          # 再自己填金鑰
python scripts/collect.py --dry-run
git worktree add data-branch data    # 展開累積的資料
```
