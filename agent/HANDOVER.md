# 交接事項 — 本機衛星影像灰區侵擾分析 Agent

> 最後更新：2026-04-19
> 專案分支：`claude/deploy-remote-sensing-agents-0hWfy`
> Repo：https://github.com/garden94030/Awesome-Remote-Sensing-Agents

---

## 1. 專案目標

- 使用 **Google Gemini API（vision 模型）** 分析本機的衛星／航照影像
- 輸出 **結構化 JSON**，可直接併入研究報告
- 主要使用情境：分析中央大學太空及遙測中心提供的 **金門衛星影像**，
  產出「灰區侵擾（gray-zone coercion）指標」初步判讀
- 工具定位：**初步判讀助手**，不取代專業分析師；模型輸出需人工複核

## 2. 目前狀態

| 項目 | 狀態 |
|------|------|
| 程式碼完成 | ✅ 已推送到 `claude/deploy-remote-sensing-agents-0hWfy` |
| 六個子指令（describe / classify / detect / change / custom / grayzone / batch）| ✅ 可用 |
| 灰區專屬 prompt（含 maritime / infrastructure / NDVI+NDWI 三類指標 + 反向解釋）| ✅ 已內建 |
| Windows 本機執行 | ⚠️ **pip install 卡關，尚未成功安裝依賴** |
| Gemini API key 設定 | ⚠️ 需去 https://aistudio.google.com/apikey 免費申請 |
| 實際跑過金門影像 | ❌ 尚未執行 |

## 3. 卡關點（最優先處理）

**Windows PowerShell 上 `pip install -r requirements.txt` 失敗**

- 第一輪錯誤：`UnicodeDecodeError: 'cp950' codec can't decode byte 0xe2`
  → 已修正（requirements.txt 改成純 ASCII，commit `4eeb885`）
- 第二輪錯誤：`ModuleNotFoundError: No module named 'PIL'`
  → 代表即使拉了最新版，pip 仍有套件建置失敗，但完整錯誤訊息被終端機滾掉沒看到

### 解法（照順序做）

```powershell
cd C:\Users\garde\Awesome-Remote-Sensing-Agents\agent
git pull
.venv\Scripts\activate

# 升級 pip，太舊的 pip 會導致某些 wheel 抓不到
python -m pip install --upgrade pip

# 分步安裝，一有錯就停下來看
pip install Pillow
pip install google-genai

# 若 google-genai 因為 C extension 編譯失敗，改用預編譯 wheel：
pip install --only-binary :all: google-genai
```

如果還失敗，把 **完整紅字錯誤** 存成檔案再看：

```powershell
pip install google-genai 2>&1 | Out-File pip_error.txt -Encoding utf8
notepad pip_error.txt
```

## 4. 執行環境檢查清單

執行前請確認：

- [ ] `python --version` ≥ 3.10（建議 3.11 或 3.12）
- [ ] `.venv` 已啟動（提示字元最前面有 `(.venv)`）
- [ ] `pip list` 能看到 `google-genai` 和 `Pillow`
- [ ] `.env` 檔存在且 `GEMINI_API_KEY=` 後面有貼上真正的 key（無引號）
- [ ] `python rs_agent.py --help` 可正常顯示說明

## 5. 執行指令範本

環境裝好後，典型使用方式：

```powershell
# 對單一場景做灰區判讀（建議做法：同時給 RGB + NDVI + NDWI）
python rs_agent.py grayzone `
  --rgb  "G:\我的雲端硬碟\...\場景A_rgb.png" `
  --ndvi "G:\我的雲端硬碟\...\場景A_ndvi.png" `
  --ndwi "G:\我的雲端硬碟\...\場景A_ndwi.png" `
  --context "金門西岸烈嶼外海, 2026年3月15日, Pleiades 0.5m, 西南為既有民用商港" `
  -o 場景A_grayzone.json

# 兩時期比對（填海／新建工程指認）
python rs_agent.py change "G:\...\場景A_2024.png" "G:\...\場景A_2026.png" -o change.json

# 批次整個資料夾做基本描述
python rs_agent.py batch "G:\...\金門衛星影像" --task describe --glob "*.png" -o all_scenes.json
```

## 6. 檔案清單（`agent/` 目錄）

| 檔案 | 用途 |
|------|------|
| `rs_agent.py` | 主程式（CLI）；含六個子指令與所有 prompt |
| `requirements.txt` | Python 依賴清單（純 ASCII） |
| `.env.example` | API key 設定範本；複製成 `.env` 後填入 key |
| `README.md` | 使用手冊 |
| `HANDOVER.md` | 本檔案 |

## 7. 準備影像素材的 QGIS 匯出步驟

針對每一個要分析的場景，在 QGIS 裡匯出三張 **同一 extent** 的 PNG：

1. 將 RGB composite、NDVI、NDWI 三個 raster layer 分別打開
2. 只顯示 **一個 layer**，其他隱藏
3. `Project` → `Import/Export` → `Export Map to Image`
4. Extent 選 `Map canvas extent`；寬度 2048 px 就夠（小影像反而細節保留較好）
5. 輸出檔名：`場景A_rgb.png`、`場景A_ndvi.png`、`場景A_ndwi.png`
6. 重複上述步驟匯出其他兩張

> ⚠️ **重要**：三張 PNG 必須是 **同一 extent、同一大小**，否則模型無法對位。
> NDVI / NDWI 的 symbology（色帶）請使用對比明顯的配色，例如 NDVI 用綠—白—紅的 diverging ramp。

## 8. `--context` 字串撰寫建議

這是影響模型輸出品質 **最大** 的單一欄位。愈具體愈好。模板：

```
<地點>, <日期>, <感測器與解析度>, <已知民用設施或地標>, <分析重點>
```

範例：
- `烈嶼鄉西南外海, 2026-03-15, Pleiades Neo 0.3m, 西岸為九宮碼頭, 關注新出現的船舶集結與海岸線變化`
- `金門本島北海岸馬山觀測所一帶, 2025-11, SPOT 1.5m, 分析植被清除與新工事`

提供得愈明確，`benign_explanations` 欄位（反向解釋）才會對得上該區域的常態，`concern_level` 才會有意義。

## 9. 成本估算（Gemini 2.5 Pro, 截至 2026-04）

- 單一場景 3 張 PNG（合計約 3 MB）+ prompt 約 2500 tokens，單次呼叫約 **US$ 0.01–0.03**
- 批次 100 張 ≈ US$ 1–3
- 免費層（AI Studio key）額度通常足夠少量研究使用
- 若額度不夠或要大量跑批次，改用 `--model gemini-2.5-flash`（便宜約 10 倍、稍弱）

## 10. 已知限制與注意事項

- **本工具不讀 QGIS 專案（.qgz）**；只讀 PNG / JPG / TIFF
- **Gemini 只看 RGB 三通道**；多光譜的 NIR / SWIR 要先在 QGIS 算好指數再匯出
- **影像 > 3072 px 邊長會被自動降採樣**（程式內 `MAX_EDGE = 3072`）
- **`describe` / `classify` 的 ESA WorldCover 類別是給一般陸域場景用的**，
  海域為主的金門影像建議改用 `grayzone` 或 `custom`
- **模型可能幻覺**：所有「數量」「座標」「設施用途」判讀都須人工複核，
  特別是 `concern_level=high` 的條目應以 `what_would_raise_confidence` 所列
  後續工作（更高解析度、SAR、多時相比對）驗證後再引用

## 11. 下一步建議（若要繼續開發）

1. **先跑通一個場景**，驗證整條流程（拿一張測試 PNG 即可）
2. 把第一批金門影像跑完 `grayzone`，檢視 JSON 輸出品質
3. 視實際輸出，調整 `TASK_PROMPTS["grayzone"]` 的指標類別或細節
4. 若要批次處理 + 報告匯出，可加一個 `report.py` 把多個 JSON 合併成 Markdown 表格
5. 若要做時間序列變遷，可寫迴圈呼叫 `change` 子指令跑兩兩配對

## 12. 相關連結

- Gemini API 金鑰申請：https://aistudio.google.com/apikey
- Gemini 模型與價格：https://ai.google.dev/gemini-api/docs/models
- Google GenAI Python SDK：https://github.com/googleapis/python-genai
- 本 repo / 分支：
  https://github.com/garden94030/Awesome-Remote-Sensing-Agents/tree/claude/deploy-remote-sensing-agents-0hWfy
- ESA WorldCover 類別定義：https://esa-worldcover.org/
