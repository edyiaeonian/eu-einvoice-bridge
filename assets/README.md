# 第三方資產來源紀錄

本目錄的檔案**並非本專案作者的作品**,而是取自官方來源的驗證資產。此文件記錄其出處、版本與授權,以符合散布義務。

---

## EN16931 驗證資產

| 項目 | 內容 |
|---|---|
| 來源 | `github.com/ConnectingEurope/eInvoicing-EN16931` |
| 版本 | `validation-1.3.16`(2026-04-10) |
| 下載檔 | `en16931-ubl-1.3.16.zip` |
| 下載網址 | `https://github.com/ConnectingEurope/eInvoicing-EN16931/releases/download/validation-1.3.16/en16931-ubl-1.3.16.zip` |
| 取得日期 | 2026-09-20 |
| **授權** | **EUPL 1.2(European Union Public Licence)— copyleft** |

### 目錄內容

| 路徑 | 說明 |
|---|---|
| `en16931/xslt/EN16931-UBL-validation.xslt` | **官方預編譯 XSLT,本專案實際執行的驗證規則** |
| `en16931/schematron/` | Schematron 原始檔(含 abstract / UBL / codelist / preprocessed) |
| `en16931/examples/` | 官方範例發票,作為測試素材 |

### 授權義務

EUPL 1.2 要求:

- 保留原始著作權聲明
- 散布時附上授權全文
- 衍生作品須以 EUPL 或相容授權散布

**本專案的處理方式**:這些檔案以未修改的原樣納入,僅由程式執行、未經改作。本專案自身程式碼的授權另行聲明於根目錄,兩者授權範圍分離。

---

## 測試素材的重要注意事項

### `BIS3_Invoice_negativ.XML` 不可作為 EN16931 core 的負面測試

實測發現(2026-09-20):

| 檔案 | CustomizationID | 觸發規則數 | failed-assert |
|---|---|---|---|
| `ubl-tc434-example1.xml` | `urn:cen.eu:en16931:2017` | 211 | 0 |
| `BIS3_Invoice_negativ.XML` | `...#compliant#...peppol.eu...billing:3.0` | 64 | **0** |

**原因**:該檔案是設計來違反 **Peppol BIS 3.0 CIUS** 規則,而非 EN16931 core 規則。本專案不實作 CIUS 層,因此 core 驗證放行它是**正確行為**。

**結論**:負面測試素材必須**自行竄改合法範例產生**,不可依賴此檔。

已驗證可用的作法:將 `ubl-tc434-example1.xml` 的 `cbc:TaxInclusiveAmount` 由 `250.33` 改為錯誤數值,可穩定觸發 `BR-CO-15` 與 `BR-CO-16` 兩條一致性規則。

---

---

## UBL 2.1 Schema(OASIS)

| 項目 | 內容 |
|---|---|
| 來源 | `docs.oasis-open.org/ubl/os-UBL-2.1/xsd/` |
| 版本 | UBL 2.1 OASIS Standard |
| 取得日期 | 2026-09-21 |
| 檔案數 | 14(含遞迴相依的 import/include) |
| 大小 | 約 2.7 MB |
| **著作權** | **Copyright (c) OASIS Open 2013. All Rights Reserved.** |

取得方式為從 `UBL-Invoice-2.1.xsd` 起遞迴解析 `schemaLocation` 直到閉合,未手動挑選,以確保相依完整。目錄結構(`xsd/maindoc/`、`xsd/common/`)維持原樣,因為 XSD 之間以相對路徑互相引用。

**授權**:依 OASIS IPR Policy,OASIS Standard 的規範文件與 schema 可自由複製與散布,條件為保留著作權聲明。各檔案檔頭的原始聲明皆未修改。

### 為何需要 XSD 層

**Schematron 通過不等於 XSD 通過。**Schematron 以 XPath 尋找元素,元素順序錯誤時通常仍能找到並通過驗證;但 UBL 的 XSD 以 sequence 定義順序,順序錯誤會被擋下。兩層檢查的是不同的東西,缺一不可。

---

## 尚待加入

- FA(3) XSD(波蘭財政部)—— 階段二需要
