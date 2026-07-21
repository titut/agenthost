# Description
Reference guide for interpreting Bé và Bạn (Bevaban) sales tracking Excel files. These standardized workbooks track daily operations across multiple Vietnamese children's entertainment center branches.

---

## File Overview

**Purpose:** Daily sales and operations tracking for children's play center branches
**Language:** Vietnamese
**Currency:** Vietnamese Đồng (VND)
**Structure:** Multi-sheet workbook with standardized sheet names across all branches
**Update Frequency:** Daily

---

## Sheet Structure

All Bevaban sales files contain 8-9 standard sheets:

| Sheet Name | Vietnamese Name | Purpose |
|------------|-----------------|---------|
| Tổng Bán | Tong Ban | Daily summary of all revenue streams |
| Nước Bán | NUOC BAN | Beverage sales tracking |
| Nghệ Thuật | NGHỆ THUẬT | Art/craft activity sales |
| Bánh Bán | BANH BAN | Snack and candy sales |
| Đồ Chơi Bán | DO CHOI BAN | Toy sales |
| Nhân Sự | NHAN SU | Employee attendance and payroll |
| Gửi Hàng | GUI HANG | Inventory transfer requests to HQ |
| Phiếu Tích Lũy | TÍCH LŨY | Customer loyalty program tracking |
| Vé Ưu Đãi | (optional) | Discount ticket usage |

---

## Sheet-by-Sheet Breakdown

### 1. Tổng Bán (Summary Sheet)

**Purpose:** Daily aggregated totals across all product categories

**Typical Columns:**
- Ngày (Date) - DD/MM/YYYY format
- Nước Bán (Beverage sales total)
- Bánh Bán (Snack sales total)
- Nghệ Thuật (Art sales total)
- Đồ Chơi (Toy sales total)
- Tổng (Grand total)

**Key Information:**
- Daily revenue by category
- Monthly and yearly totals
- Comparison across days

---

### 2. Nước Bán / NUOC BAN (Beverage Sales)

**Purpose:** Track all beverage sales and inventory

**Common Products:**
| Product | Description |
|---------|-------------|
| Suối / Suối Lớn | Bottled water (large) |
| C2 / Ô long | Tea drinks (C2 brand, Oolong tea) |
| Sâm | Ginseng drink |
| Coca / Pepsi / Fanta | Soft drinks |
| Mirinda Xá xị | Sarsi-flavored soda |
| Sting | Energy drink |

**Column Structure:**
```
Ngày | Sản Phẩm | ĐVT | Giá Bán | SL Bán | Thành Tiền | Nhập | Xuất | Tồn
```

**Column Definitions:**
- **Ngày** - Date of transaction
- **Sản Phẩm** - Product name
- **ĐVT** - Unit (usually "Chai" = bottle)
- **Giá Bán** - Selling price per unit (VND)
- **SL Bán** - Quantity sold
- **Thành Tiền** - Total revenue (Giá Bán × SL Bán)
- **Nhập** - Quantity received/stocked in
- **Xuất** - Quantity sold/out
- **Tồn** - Current inventory balance

---

### 3. NGHỆ THUẬT (Art Activities)

**Purpose:** Track art and craft activity sales

**Common Products:**
| Product | Description | Typical Price |
|---------|-------------|---------------|
| Tranh Lớn | Large painting | 50,000-100,000 VND |
| Tranh Nhỏ | Small painting | 25,000-50,000 VND |
| Tranh Đá | Sand painting | 35,000-70,000 VND |
| Tranh Số | Paint-by-numbers | 40,000-80,000 VND |
| Tượng Lớn | Large statue/craft | 60,000-100,000 VND |
| Tượng Trung | Medium statue | 40,000-60,000 VND |
| Tượng Đôi | Pair statue | 50,000-80,000 VND |
| Tượng Nhỏ | Small statue | 25,000-40,000 VND |
| Cát Tô | Sand art container | 30,000-50,000 VND |
| Hạt Nhựa | Plastic beads | 20,000-35,000 VND |

**Special Columns:**
- **Hoa Hồng** - Commission/points earned (loyalty program)
- **Phiếu TL** - Loyalty vouchers used
- **Tích Lũy** - Accumulated points

---

### 4. BANH BAN (Snack Sales)

**Purpose:** Track snack and candy sales

**Common Products:**
- Various cakes (bánh)
- Candies (kẹo)
- Que Lớn / Que Nhỏ (large/small sticks - likely corn sticks or similar)
- Snack packs
- Ice cream (kem)

**Structure:** Similar to Nước Bán with Ngày, Sản Phẩm, SL Bán, Thành Tiền, Nhập, Xuất, Tồn

---

### 5. DO CHOI BAN (Toy Sales)

**Purpose:** Track toy sales and prize redemptions

**Products:**
- Small toys
- Gift items (Quà)
- Prize redemption items

**Note:** Often includes items given as rewards from art activities

---

### 6. NHAN SU (Employee Management)

**Purpose:** Track employee attendance, shifts, and overtime

**Column Structure:**
```
Ngày | Tên | SĐT | Ca | Giờ Tăng Ca | Ghi Chú
```

**Column Definitions:**
- **Ngày** - Work date
- **Tên** - Employee full name
- **SĐT** - Phone number
- **Ca** - Shift (usually numbered: 1, 2, 3 or morning/afternoon/evening)
- **Giờ Tăng Ca** - Overtime hours worked
- **Ghi Chú** - Notes (absence, late, etc.)

**Common Roles:**
- Quản Lý (Manager)
- Thu Ngân (Cashier)
- Soát Vé (Ticket checker)
- Nhân Viên (General staff)

**Payroll Information:**
- Usually found at bottom of sheet
- Shows Ứng (advances) and remaining salary
- Calculated based on shifts worked + overtime

---

### 7. GUI HANG (Inventory Transfers)

**Purpose:** Request and track goods sent from HQ to branch

**Column Structure:**
```
Ngày | Sản Phẩm | Số Lượng | Đơn Vị | Ghi Chú
```

**Usage:**
- Branch requests stock from headquarters
- Tracks what was sent and when
- Used for inventory replenishment

---

### 8. TÍCH LŨY / Phiếu Tích Lũy (Loyalty Program)

**Purpose:** Track customer loyalty cards and rewards

**Structure:**
- Customer names or card numbers
- Visit dates
- Points accumulated
- Rewards redeemed
- Card status (active/inactive)

---

## Common Patterns & Calculations

### Revenue Calculation
```
Thành Tiền = Giá Bán × SL Bán
```

### Inventory Formula
```
Tồn = Tồn Đầu + Nhập - Xuất
```

### Daily Total
```
Tổng Doanh Thu = Nước + Bánh + Nghệ Thuật + Đồ Chơi
```

### Employee Pay
```
Lương = (Ca Làm × Rate) + (Giờ Tăng Ca × Overtime Rate) - Ứng
```

---

## Data Quality Indicators

### Red Flags to Watch For:
1. **Negative inventory (Tồn < 0)** - Indicates data entry error or missing receipt
2. **Zero prices** - Missing price data
3. **Unusual quantities** - Extremely high/low sales compared to average
4. **Missing dates** - Gaps in daily tracking
5. **Mismatched totals** - Tổng Bán doesn't sum individual sheets

### Validation Checks:
- Tổng Bán should equal sum of category sheets
- Inventory should never go negative
- Employee shifts should match store operating hours
- Dates should be sequential without gaps

---

## Branch-Specific Variations

| Branch | Characteristics |
|----------|-----------------|
| Sóc Trăng | Full product range, detailed tracking |
| Thốt Nốt | Large branch, extensive snack variety |
| Tiểu Cần | Smaller operation, simplified tracking |
| Duyên Hải | Comprehensive employee tracking |
| Cư M'gar | Minimal product range, basic tracking |

---

## File Naming Conventions

Typical patterns:
- `BÁO CÁO BÁN HÀNG [BRANCH] THÁNG [MM].xlsx`
- `DU LIEU BAN HANG [YEAR] [BRANCH].xlsx`
- `Dữ liệu bán hàng [BRANCH] [YEAR].xlsx`

---

## Tips for Analysis

1. **Always check Tổng Bán first** for daily overview
2. **Verify inventory balances** match physical counts
3. **Compare branches** using same time periods
4. **Track seasonal patterns** (holidays, weekends)
5. **Monitor employee overtime** for labor cost control
6. **Cross-reference** Phiếu Tích Lũy with Nghệ Thuật sales
7. **Check Gửi Hàng** to understand stock replenishment cycles

---

## Related Information

- **Currency:** All amounts in Vietnamese Đồng (VND)
- **Date Format:** DD/MM/YYYY
- **Language:** Vietnamese with some English product names
- **Business Type:** Children's entertainment/play centers
- **Typical Location:** Shopping malls, entertainment complexes
