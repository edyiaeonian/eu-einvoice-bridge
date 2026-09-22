# 階段三實作紀錄:加密、KSeF 客戶端與送件

**日期**:2026-09-21
**對應規格**:`docs/specs/2026-09-20-ksef-en16931-bridge-design.md` 第 3、9、10、11 節
**階段目標**:把 FA(3) 發票加密後送入波蘭 KSeF 2.0 TEST 環境,取回 KSeF 編號與官方收據(UPO);中途當掉可續傳,且絕不重送。

**狀態:✅ 已完成(2026-09-21)**——六項驗收標準全數達成,已對真實 KSeF TEST 環境實測通過。

> 本階段的設計原則——只重試冪等操作、送件前先寫入狀態、絕不自動重送——沿用先前審定的規格第 10、11 節。程式實作以 AI 輔助完成;實作中偏離規格之處(認證改用 XAdES 自簽憑證)經作者審閱後採用。因此本文件以「紀錄」而非「計畫」的形式呈現:寫下實際做了什麼、依據什麼證據決定。

---

## 驗收標準與結果

| # | 標準 | 結果 | 證據 |
|---|---|---|---|
| 1 | `crypto` 以固定測試向量驗證 AES-256-CBC 與 RSA-OAEP | ✅ | `tests/test_crypto.py`:NIST SP 800-38A 向量;SHA-1 OAEP 無法解開,證明確實用 SHA-256 |
| 2 | 沙箱認證、開 session、送出加密發票 | ✅ | `tests/test_ksef_live.py` 實測通過 |
| 3 | 輪詢並下載 UPO,寫入本地狀態檔 | ✅ | 同上;UPO 命名空間驗證為 `KSeF/v4-3` |
| 4 | 中斷後憑狀態檔續查,不重送 | ✅ | `tests/test_ksef_submit.py::TestAnUnansweredSend` 等 |
| 5 | 狀態機以 `respx` 離線測試 | ✅ | 23 個離線測試,預設執行 |
| 6 | 整合測試對真實 TEST 走完全程 | ✅ | `pytest -m integration`,約 4 秒 |

---

## 第 0 步:先驗證風險最高的假設,再寫正式程式

寫正式程式之前,先用丟棄式探測腳本(不在 repo 內)對真實 TEST 環境確認三件事:

| 假設 | 結果 |
|---|---|
| TEST 接受自簽憑證的 XAdES 認證 | ✅ 一次成功(`Uwierzytelnianie zakończone sukcesem`) |
| 加密 → 送件 → 輪詢 → UPO 全程可走通 | ✅ 狀態 150 → 200 `Sukces`,取得 KSeF 編號,UPO 5480 bytes |
| 本專案產出的 FA(3) 能通過 KSeF 伺服器端驗證 | ✅ 伺服器端語意驗證通過——這比本機 XSD 更嚴格 |

第三點特別重要:階段二只證明了「通過 XSD」,這是第一次證明「KSeF 本身接受」。

---

## 關鍵決策

### 1. 認證改用 XAdES 自簽憑證(偏離原規格)

原規格寫「只做 KSeF token,不做 XAdES」。查 OpenAPI 發現 `POST /tokens` 需要 Bearer 認證——**token 只能在已認證後產生**,第一次認證不可能用 token。XAdES 自簽是 TEST 環境唯一能無人值守的路徑。詳見規格第 9 節「偏離原設計」。

### 2. 只重試冪等操作

`KsefClient` 內部分兩條路徑:`_idempotent`(指數退避重試)與 `_send`(不重試)。

| 重試 | 不重試 | 原因 |
|---|---|---|
| challenge、查認證狀態、取公鑰、查發票狀態、列 session 發票、下載 UPO | **送件**、開 session、兌換 token、XAdES 提交、關 session | 送件重複會產生重複發票;兌換 token 是一次性的,第二次必定失敗 |

以突變測試驗證:把送件暫時改成走重試路徑,4 個測試立即失敗。

### 3. 送出之前先寫狀態

最危險的時刻是「送件請求已發出,回應沒回來」。此時手上沒有發票參考編號,不知道 KSeF 收到沒有。

- 送出前寫入 `status: sending` 與 session 參考編號
- 重跑時看到 `sending`,就**列出該 session 的發票,以雜湊值比對**
- 找到 → 繼續輪詢;找不到 → 拋出 `SubmissionUncertain`,**停下交給人判斷**,不猜

KSeF 本身也有第二道防線:狀態碼 440「重複發票」。但本專案不依賴它。

### 4. 保存實際送出的 XML

FA(3) 內含產生時間(`DataWytworzeniaFa`),每次產生的位元組都不同。若續傳時重新產生,雜湊值就對不上 session 裡那張,查詢會落空。因此:

- 第一次送件前,把確切的 XML 存到 `state/<編號>.fa3.xml`
- 衝突偵測改用**輸入內容的雜湊值**(同一份 JSON 永遠得到同一個值),不用 XML 雜湊

這是寫測試時發現的:原本的設計用 XML 雜湊偵測衝突,同一張發票重跑會被誤判為「內容不同」。

### 5. 4xx 與 5xx 分開處理

送件時:
- **5xx / 逾時**:可能已送達 → 紀錄維持 `sending`,下次重跑去查
- **4xx**:KSeF 明確拒絕 → 紀錄標為 `rejected`,不必再查

### 6. 賣方必須是認證的 NIP

KSeF 只接受「認證身分就是賣方」的發票。CLI 預設在賣方 NIP 不符時拒送並提示;加 `--test-seller` 才以測試身分的 NIP 取代。這避免使用者誤以為範例檔可以直接送。

---

## 實作內容

| 檔案 | 內容 |
|---|---|
| `src/eu_einvoice_bridge/crypto/__init__.py` | AES-256-CBC/PKCS#7、RSA-OAEP(SHA-256/MGF1-SHA-256)、從 base64 DER 憑證取公鑰;`SessionKey` 的 repr 遮蔽金鑰 |
| `src/eu_einvoice_bridge/ksef/identity.py` | NIP 檢查碼、隨機測試 NIP、自簽印章憑證、XAdES-BES 簽章 `AuthTokenRequest`、存讀身分(私鑰權限 600) |
| `src/eu_einvoice_bridge/ksef/client.py` | KSeF 2.0 端點;冪等/非冪等兩條路徑;session 發票清單跟隨分頁 |
| `src/eu_einvoice_bridge/ksef/submit.py` | `StateStore`(原子寫入)、認證、送件、復原、輪詢、下載 UPO |
| `src/eu_einvoice_bridge/cli/main.py` | `einvoice submit` 指令 |

### CLI 結束碼

| 碼 | 意義 |
|---|---|
| 0 | KSeF 接受,已取得 UPO |
| 1 | 發票不合格(本機檢查失敗,或 KSeF 拒絕) |
| 2 | 檔案無法讀取 |
| 3 | 已送出但仍在處理中,重跑同一指令即可 |
| 4 | 送件本身失敗(網路、認證、無法確認先前送件) |

---

## 測試

| 檔案 | 數量 | 內容 |
|---|---|---|
| `test_crypto.py` | 14 | NIST 向量、OAEP 雜湊、repr 遮蔽 |
| `test_ksef_identity.py` | 18 | NIP 檢查碼、憑證格式、以 `signxml` 驗章、竄改後驗章失敗 |
| `test_ksef_submit.py` | 23 | 快樂路徑、解密送出的內容、送出前已寫狀態、無回應不重試、雜湊查詢、分頁、不確定、4xx/5xx、衝突、逾時續查、退避 |
| `test_cli_submit.py` | 9 | 賣方檢查、不合格不送、結束碼 |
| `test_ksef_live.py` | 1 | 真實 TEST 環境(預設跳過) |

全套:**359 passed, 1 deselected**(離線)。

---

## 實測紀錄(2026-09-21,華沙時間約 20:30)

整合測試與 CLI 各實跑一次,皆使用當次隨機產生的身分與買方 NIP:

```
$ einvoice submit invoice.json --state-dir … --identity-dir …
created a self-signed KSeF TEST identity for NIP 5046948298 in …/certs/
invoice.json: seller.vat_id is PL5555555555, but the test identity is PL5046948298; …
                                                        (exit 1,未送出)
$ einvoice submit invoice.json … --test-seller
invoice.json: accepted by KSeF TEST
  KSeF number: 5046948298-20260921-906675C00000-7C
  UPO:         …/state/CLI_a555c6.upo.xml               (exit 0)
$ einvoice submit invoice.json … --test-seller           (重跑)
invoice.json: accepted by KSeF TEST                      (直接從紀錄回答,未發出請求)
```

---

## 已知限制(本階段新增)

- 僅 TEST 環境;base URL 寫死
- 不驗證 UPO 的 XAdES 簽章(僅儲存)
- `SubmissionUncertain` 之後需人工判斷,沒有提供「確認後強制重送」的指令——刻意不做,避免一個旗標就繞過防重送
- 認證每次重跑都重新進行,不快取 access token(token 只有 15 分鐘,且快取需要把 token 寫到磁碟)
