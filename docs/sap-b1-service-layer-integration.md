# SAP B1 Service Layer Integration Notes

## Connection Details

- **Service Layer URL**: `https://182.73.7.81:50000/b1s/v1`
- **Authentication**: POST to `/Login` with `CompanyDB`, `UserName`, `Password`
- **Session**: Cookie-based (`B1SESSION`, `ROUTEID`)
- **SSL**: Self-signed certificate (use `--insecure` / disable SSL verification)

## OITM Table — Product Master

The primary table for querying product/item data is `OITM` (Items Master Data).

### Confirmed Column List (from SAP B1)

| Column | Description |
|--------|-------------|
| `ItemCode` | Item code (primary key) |
| `ItemName` | Item name / description |
| `FrgnName` | Foreign name |
| `CodeBars` | Barcode |
| `U_BND` | Brand (UDF) |
| `U_FLAVOR` | Flavor (UDF) |
| `U_ITPK` | Item pack type (UDF) |
| `U_MRP` | MRP price (UDF) |
| `SalUnitMsr` | Sales unit of measure |
| `SalPackUn` | Sales packaging unit |
| `OnHand` | Current stock on hand |
| `U_HSN_Code` | HSN code (UDF) |
| `ItmsGrpCod` | Item group code (numeric) |
| `validFor` | Whether item is active (`Y`/`N`) |
| `frozenFor` | Whether item is frozen (`Y`/`N`) |
| `Canceled` | Whether item is canceled (`Y`/`N`) |

> **Important**: The column is `ItmsGrpCod` (with a **d**), NOT `ItmsGrpCode`.
> There is no `ItmsGrpNam` column on OITM — group names live in the `OITG` table.

### Working Query — Fetch Active Items

```sql
SELECT TOP 1000
    ItemCode,
    ItemName,
    FrgnName,
    CodeBars,
    U_BND,
    U_FLAVOR,
    U_ITPK,
    U_MRP,
    SalUnitMsr,
    SalPackUn,
    OnHand,
    U_HSN_Code,
    ItmsGrpCod
FROM OITM
WHERE validFor = 'Y'
  AND frozenFor = 'N'
  AND Canceled = 'N'
ORDER BY ItemCode
```

### Service Layer Equivalent (OData)

```
GET /b1s/v1/Items?$select=ItemCode,ItemName,ForeignName,BarCode,U_BND,U_FLAVOR,U_ITPK,U_MRP,SalesUnit,SalesPackagingUnit,QuantityOnStock,U_HSN_Code,ItemsGroupCode&$filter=Valid eq 'tYES' and Frozen eq 'tNO' and Cancelled eq 'tNO'&$top=1000
```

> **Note**: Service Layer uses different property names than the SQL column names:
> - `FrgnName` → `ForeignName`
> - `CodeBars` → `BarCode`
> - `SalUnitMsr` → `SalesUnit`
> - `SalPackUn` → `SalesPackagingUnit`
> - `OnHand` → `QuantityOnStock`
> - `ItmsGrpCod` → `ItemsGroupCode`

## Authentication Flow

```bash
# 1. Login
curl -k -X POST "https://182.73.7.81:50000/b1s/v1/Login" \
  -H "Content-Type: application/json" \
  -d '{"CompanyDB":"TJUKDB","UserName":"manager","Password":"****"}' \
  -c cookies.txt

# 2. Query Items (using session cookie)
curl -k -X POST "https://182.73.7.81:50000/b1s/v1/SQLQueries('itemQuery')/List" \
  -b cookies.txt \
  -H "Content-Type: application/json"
```

## Key Learnings & Gotchas

1. **Column name precision matters** — `ItmsGrpCod` not `ItmsGrpCode`, `frozenFor` not `FrozenFor`
2. **Self-signed SSL cert** — Must disable SSL verification when connecting
3. **Session expiry** — B1 sessions timeout; re-login if you get 401
4. **UDF columns** — All `U_*` columns are user-defined fields specific to this tenant
5. **OITG for group names** — To get item group names, join OITM.ItmsGrpCod = OITG.ItmsGrpCod
6. **Service Layer vs SQL** — Property names differ between OData API and direct SQL

## Next Steps

- [ ] Build product sync pipeline (fetch OITM → normalize → cache locally)
- [ ] Map item group codes to group names via OITG
- [ ] Integrate product catalog into WhatsApp ordering bot
- [ ] Handle stock checks (OnHand) for order validation
