"""
Cohort LTV & CAC payback: Python version of the BigQuery pipeline, plus charts.

Reads the 4 CSVs in ../data, applies the same cleaning and cohort logic as the
SQL files (monthly signup cohorts, contribution-margin LTV, fully loaded CAC),
prints the channel summary and saves 4 charts to python/charts.

Run:  python python/cohort_ltv_charts.py      (from the repo root)
Needs: pandas>=2.0, matplotlib  (see requirements.txt)
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Settings (same constants as the SQL files)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CHART_DIR = REPO_ROOT / "python" / "charts"

DATA_END_MONTH = pd.Timestamp("2026-09-01")   # last full month of orders
PAYMENT_FEE_RATE = 0.03                       # card processing, % of net revenue
LTV_COHORTS = (pd.Timestamp("2024-10-01"), pd.Timestamp("2025-09-01"))  # cohorts with 12 full months
PAID_CHANNELS = ["Google Ads", "Meta Ads", "TikTok Ads"]
ALL_CHANNELS = PAID_CHANNELS + ["Organic / Direct", "Referral"]

CHANNEL_COLORS = {            # one fixed color per channel, used in every chart
    "Google Ads": "#2a78d6",
    "Meta Ads": "#eb6834",
    "TikTok Ads": "#1baf7a",
    "Organic / Direct": "#eda100",
    "Referral": "#e87ba4",
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "figure.dpi": 150,
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": MUTED,
    "axes.titlecolor": INK,
    "axes.titleweight": "bold",
    "axes.titlesize": 12,
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "legend.frameon": False,
})


# ---------------------------------------------------------------------------
# 1. Load and clean (same rules as 02_udfs.sql and 03_staging.sql)
# ---------------------------------------------------------------------------
def to_num(series):
    """Text -> number. Strips spaces, thousands separators and currency symbols."""
    cleaned = series.fillna("").astype(str).str.replace(r"[,$\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def to_date(series):
    """Text -> date. Accepts 2025-03-04, 03/04/2025, 3/4/2025 and 2025-03-04T14:22:00Z."""
    s = series.astype(str).str.strip().str.slice(0, 10).str.replace(r"T.*$", "", regex=True)
    return pd.to_datetime(s, format="mixed", errors="coerce").dt.normalize()


def to_month(series):
    """Ledger month -> first day of month. Accepts 2025-03, Mar-2025 and 3/1/2025."""
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s + "-01", format="%Y-%m-%d", errors="coerce")
    mon = pd.to_datetime(s, format="%b-%Y", errors="coerce")
    us = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")
    return iso.fillna(mon).fillna(us).dt.to_period("M").dt.to_timestamp()


def map_channel(source, medium):
    """utm_source + utm_medium -> channel. Medium is checked first so google/organic is not paid."""
    src, med = str(source).strip().lower(), str(medium).strip().lower()
    if src in ("", "nan", "(direct)") or med in ("organic", "email", "(none)"):
        return "Organic / Direct"
    if "referral" in med or "refer" in src or src == "friendbuy":
        return "Referral"
    if src in ("google", "adwords"):
        return "Google Ads"
    if src in ("facebook", "fb", "ig", "instagram", "meta"):
        return "Meta Ads"
    if src in ("tiktok", "tt"):
        return "TikTok Ads"
    return "Other"


def map_ledger_channel(label):
    s = str(label).strip().lower()
    if "google" in s:
        return "Google Ads"
    if "meta" in s or "facebook" in s:
        return "Meta Ads"
    if "tiktok" in s:
        return "TikTok Ads"
    return "Non-acquisition"


def load_customers():
    raw = pd.read_csv(DATA_DIR / "customers.csv", dtype=str, keep_default_na=False)
    cust = pd.DataFrame({
        "customer_id": raw["customer_id"].str.strip().str.upper(),
        "signup_date": to_date(raw["signup_date"]),
        "channel": [map_channel(s, m) for s, m in zip(raw["utm_source"], raw["utm_medium"])],
    })
    # duplicate export rows: keep one row per customer (earliest signup)
    cust = cust.sort_values(["customer_id", "signup_date"]).drop_duplicates("customer_id")
    cust["cohort_month"] = cust["signup_date"].dt.to_period("M").dt.to_timestamp()
    return cust.reset_index(drop=True), len(raw)


def load_orders(plans):
    raw = pd.read_csv(DATA_DIR / "orders.csv", dtype=str, keep_default_na=False)
    status = raw["status"].str.strip().str.lower()
    o = pd.DataFrame({
        "order_id": raw["order_id"].str.strip(),
        "customer_id": raw["customer_id"].str.strip().str.upper(),
        "order_date": to_date(raw["order_date"]),
        "plan_id": raw["plan_id"].str.strip().str.upper(),
        "gross_amount": to_num(raw["gross_amount"]),
        "discount": to_num(raw["discount"]).fillna(0),
        "status": np.select([status.str.startswith("complete"), status.str.startswith("refund"),
                             status.str.startswith("cancel")],
                            ["completed", "refunded", "cancelled"], "unknown"),
    })
    n_raw = len(o)
    o = o[~o["customer_id"].str.startswith("TEST")]          # internal test orders
    o = o.drop_duplicates("order_id")                         # overlapping exports
    o = o[o["status"] != "cancelled"]                         # failed payments: nothing shipped
    o = o.merge(plans, on="plan_id", how="left")
    o["net_revenue"] = np.where(o["status"] == "refunded", 0.0, o["gross_amount"] - o["discount"])
    o["contribution"] = (o["net_revenue"] - o["cogs_per_box"] - o["shipping_per_box"]
                         - o["net_revenue"] * PAYMENT_FEE_RATE)
    o["order_month"] = o["order_date"].dt.to_period("M").dt.to_timestamp()
    return o.reset_index(drop=True), n_raw


def load_spend():
    raw = pd.read_csv(DATA_DIR / "marketing_spend.csv", dtype=str, keep_default_na=False)
    return pd.DataFrame({
        "month": to_month(raw["month"]),
        "channel": raw["channel"].map(map_ledger_channel),
        "category": raw["category"].str.strip(),
        "amount": to_num(raw["amount"]),
    })


def load_plans():
    p = pd.read_csv(DATA_DIR / "plans.csv", dtype=str)
    return pd.DataFrame({
        "plan_id": p["plan_id"].str.strip().str.upper(),
        "cogs_per_box": to_num(p["cogs_per_box"]),
        "shipping_per_box": to_num(p["shipping_per_box"]),
    })


# ---------------------------------------------------------------------------
# 2. Cohort model (same logic as 05_cohort_model.sql)
# ---------------------------------------------------------------------------
def months_between(later, earlier):
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


def build_cohorts(cust, orders):
    """One row per cohort_month x channel x month_number, including months with zero orders."""
    sizes = cust.groupby(["cohort_month", "channel"]).size().rename("cohort_size").reset_index()

    o = orders.merge(cust[["customer_id", "cohort_month", "channel"]], on="customer_id", how="inner")
    o["month_number"] = months_between(o["order_month"], o["cohort_month"])
    activity = (o.groupby(["cohort_month", "channel", "month_number"])
                 .agg(active_customers=("customer_id", "nunique"),
                      net_revenue=("net_revenue", "sum"),
                      contribution=("contribution", "sum"))
                 .reset_index())

    grid = sizes.copy()
    grid["max_month"] = months_between(pd.Series([DATA_END_MONTH] * len(grid)), grid["cohort_month"])
    grid = grid.loc[grid.index.repeat(grid["max_month"] + 1)]
    grid["month_number"] = grid.groupby(["cohort_month", "channel"]).cumcount()
    grid = grid.merge(activity, on=["cohort_month", "channel", "month_number"], how="left").fillna(
        {"active_customers": 0, "net_revenue": 0, "contribution": 0})
    return grid.drop(columns="max_month").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Reporting (same logic as 06_reporting_views.sql)
# ---------------------------------------------------------------------------
def cac_by_cohort(cust, spend):
    """New customers and acquisition spend per channel and month (media and fully loaded)."""
    acq = spend[spend["channel"].isin(PAID_CHANNELS)]
    media = acq[acq["category"] == "Media"].groupby(["month", "channel"])["amount"].sum().rename("media_spend")
    loaded = acq.groupby(["month", "channel"])["amount"].sum().rename("loaded_spend")
    new = cust.groupby(["cohort_month", "channel"]).size().rename("new_customers")
    new.index = new.index.set_names(["month", "channel"])
    out = pd.concat([new, media, loaded], axis=1).fillna(0).reset_index()
    return out


def channel_ltv_curves(cohorts):
    """Cumulative contribution per acquired customer, months 0-11, cohorts with 12 full months."""
    c = cohorts[cohorts["cohort_month"].between(*LTV_COHORTS) & (cohorts["month_number"] <= 11)]
    by_month = c.groupby(["channel", "month_number"]).agg(
        contribution=("contribution", "sum"), active=("active_customers", "sum")).reset_index()
    size = (c[c["month_number"] == 0].groupby("channel")["cohort_size"].sum())
    by_month["cohort_size"] = by_month["channel"].map(size)
    by_month["retention"] = by_month["active"] / by_month["cohort_size"]
    by_month["cum_contribution_per_customer"] = (by_month.groupby("channel")["contribution"].cumsum()
                                                 / by_month["cohort_size"])
    return by_month


def channel_summary(cust, cohorts, cac):
    curves = channel_ltv_curves(cohorts)
    in_window = cac[cac["month"].between(*LTV_COHORTS)]
    agg = in_window.groupby("channel")[["new_customers", "media_spend", "loaded_spend"]].sum()

    rows = []
    for ch in ALL_CHANNELS:
        cv = curves[curves["channel"] == ch].set_index("month_number")
        r = {"channel": ch, "customers": int(cv["cohort_size"].iloc[0])}
        r["m1_retention"] = cv.loc[1, "retention"]
        r["m6_retention"] = cv.loc[6, "retention"]
        r["ltv_3m"] = cv.loc[2, "cum_contribution_per_customer"]
        r["ltv_6m"] = cv.loc[5, "cum_contribution_per_customer"]
        r["ltv_12m"] = cv.loc[11, "cum_contribution_per_customer"]
        if ch in PAID_CHANNELS:
            r["media_cac"] = agg.loc[ch, "media_spend"] / agg.loc[ch, "new_customers"]
            r["loaded_cac"] = agg.loc[ch, "loaded_spend"] / agg.loc[ch, "new_customers"]
            r["ltv_cac_12m"] = r["ltv_12m"] / r["loaded_cac"]
            paid_back = cv.index[cv["cum_contribution_per_customer"] >= r["loaded_cac"]]
            r["payback_month"] = int(paid_back.min()) + 1 if len(paid_back) else None  # months of orders
        rows.append(r)
    return pd.DataFrame(rows).set_index("channel"), curves


def promo_cohorts(cohorts):
    """Black Friday (November, 50% off first box) cohorts vs all other cohorts with 6+ months of data."""
    c = cohorts[cohorts["cohort_month"] <= DATA_END_MONTH - pd.DateOffset(months=6)].copy()
    c["cohort_type"] = np.where(c["cohort_month"].dt.month == 11, "Black Friday (Nov)", "Other months")
    size = c[c["month_number"] == 0].groupby("cohort_type")["cohort_size"].sum()
    active = c.pivot_table(index="cohort_type", columns="month_number", values="active_customers", aggfunc="sum")
    contrib6 = c[c["month_number"] <= 5].groupby("cohort_type")["contribution"].sum()
    return pd.DataFrame({"customers": size, "m1_retention": active[1] / size,
                         "m6_retention": active[6] / size, "ltv_6m": contrib6 / size})


# ---------------------------------------------------------------------------
# 4. Charts
# ---------------------------------------------------------------------------
def save(fig, name):
    CHART_DIR.mkdir(exist_ok=True)
    fig.tight_layout()
    fig.savefig(CHART_DIR / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"saved charts/{name}")


def chart_retention_heatmap(cohorts):
    allch = (cohorts.groupby(["cohort_month", "month_number"])[["active_customers", "cohort_size"]]
             .sum().reset_index())
    allch["retention"] = allch["active_customers"] / allch["cohort_size"]
    grid = allch.pivot(index="cohort_month", columns="month_number", values="retention")
    grid = grid.loc[:, :12]

    fig, ax = plt.subplots(figsize=(9, 7.5))
    masked = np.ma.masked_invalid(grid.values)
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("white")
    ax.imshow(masked, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    for (i, j), v in np.ndenumerate(grid.values):
        if not np.isnan(v) and j > 0:
            ax.text(j, i, f"{v:.0%}", ha="center", va="center", fontsize=6.5,
                    color="white" if v > 0.55 else INK)
    ax.set_xticks(range(grid.shape[1]), [str(c) for c in grid.columns])
    ax.set_yticks(range(len(grid)), [d.strftime("%b %Y") for d in grid.index])
    for i, d in enumerate(grid.index):
        if d.month == 11:
            ax.get_yticklabels()[i].set_fontweight("bold")
            ax.get_yticklabels()[i].set_color(INK)
    ax.grid(False)
    ax.set_xlabel("Months since signup (0 = first box)")
    ax.set_title("Share of each signup cohort still ordering, by month\n"
                 "Black Friday cohorts (bold) drop off fastest")
    save(fig, "01_retention_heatmap.png")


def end_labels(ax, x, ys, labels, min_gap):
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    placed = list(ys)
    for prev, cur in zip(order, order[1:]):
        placed[cur] = max(placed[cur], placed[prev] + min_gap)
    for y, yp, (text, color) in zip(ys, placed, labels):
        ax.annotate(text, (x, yp), xytext=(8, 0), textcoords="offset points", va="center",
                    color=INK, fontsize=9)


def chart_retention_curves(curves):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ends, labels = [], []
    for ch in ALL_CHANNELS:
        cv = curves[curves["channel"] == ch]
        ax.plot(cv["month_number"], cv["retention"], color=CHANNEL_COLORS[ch], linewidth=2,
                marker="o", markersize=4)
        ends.append(cv["retention"].iloc[-1])
        labels.append((f"{ch}  {cv['retention'].iloc[-1]:.0%}", CHANNEL_COLORS[ch]))
    end_labels(ax, 11, ends, labels, 0.06)
    ax.set_xlim(0, 13.8)
    ax.set_ylim(0, 1.05)
    ax.set_xticks(range(12))
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(1.0))
    ax.set_xlabel("Months since signup")
    ax.set_ylabel("Customers still ordering")
    ax.set_title("TikTok customers churn fastest: about 1 in 25 still orders in month 11")
    ax.grid(axis="x", visible=False)
    save(fig, "02_retention_by_channel.png")


def chart_payback(curves, summary):
    fig, ax = plt.subplots(figsize=(8, 4.8))
    x = np.arange(1, 13)  # months of orders received
    for ch in PAID_CHANNELS:
        cv = curves[curves["channel"] == ch]
        color = CHANNEL_COLORS[ch]
        cac = summary.loc[ch, "loaded_cac"]
        ax.plot(x, cv["cum_contribution_per_customer"], color=color, linewidth=2)
        ax.axhline(cac, color=color, linewidth=1, linestyle="--")
        pb = summary.loc[ch, "payback_month"]
        if pd.notna(pb):
            ax.plot(pb, cv["cum_contribution_per_customer"].iloc[int(pb) - 1], marker="o", markersize=8,
                    color=color, markeredgecolor="white", markeredgewidth=2, zorder=4)
        status = f"pays back in month {int(pb)}" if pd.notna(pb) else "not paid back after 12 months"
        ax.annotate(f"{ch}: CAC ${cac:,.0f}, {status}", (12, cv["cum_contribution_per_customer"].iloc[-1]),
                    xytext=(8, 0), textcoords="offset points", va="center", color=INK, fontsize=9)
    ax.set_xticks(x)
    ax.set_xlim(0.5, 12.3)
    ax.set_ylim(0, None)
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))
    ax.set_xlabel("Months of orders since signup")
    ax.set_ylabel("Cumulative contribution margin\nper acquired customer")
    ax.set_title("Google costs the most per customer and pays back fastest\n"
                 "Dashed line = fully loaded CAC; dot = payback month")
    ax.grid(axis="x", visible=False)
    save(fig, "03_payback_curves.png")


def chart_cac_vs_ltv(summary):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(PAID_CHANNELS))
    width = 0.26
    series = [("media_cac", "Media CAC", "#b7cdea"),
              ("loaded_cac", "Fully loaded CAC", "#6f9bd6"),
              ("ltv_12m", "12-month LTV (contribution)", "#1f4f8f")]
    for i, (col, label, color) in enumerate(series):
        vals = summary.loc[PAID_CHANNELS, col]
        bars = ax.bar(x + (i - 1) * width, vals, width=width - 0.02, color=color, label=label)
        ax.bar_label(bars, labels=[f"${v:,.0f}" for v in vals], padding=2, fontsize=8, color=MUTED)
    for xi, ch in zip(x, PAID_CHANNELS):
        ax.text(xi, summary["ltv_12m"].max() * 1.13, f"LTV:CAC {summary.loc[ch, 'ltv_cac_12m']:.2f}",
                ha="center", color=INK, fontsize=9, fontweight="bold")
    ax.set_xticks(x, PAID_CHANNELS)
    ax.set_ylim(0, summary["ltv_12m"].max() * 1.22)
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("${x:,.0f}"))
    ax.set_ylabel("$ per acquired customer")
    ax.set_title("Cheapest to acquire is not best value: TikTok returns half its CAC")
    ax.legend(loc="upper center", ncols=3, bbox_to_anchor=(0.5, -0.08))
    ax.grid(axis="x", visible=False)
    save(fig, "04_cac_vs_ltv.png")


def mix_shift(cust, cac, summary):
    """Budget shift: paid mix, blended CAC and mix-weighted expected 12-month LTV, first vs last 6 months."""
    periods = {"Oct 2024 - Mar 2025": ("2024-10-01", "2025-03-01"),
               "Apr 2026 - Sep 2026": ("2026-04-01", "2026-09-01")}
    rows = []
    for name, (a, b) in periods.items():
        w = cac[cac["month"].between(a, b) & cac["channel"].isin(PAID_CHANNELS)].groupby("channel").sum(numeric_only=True)
        share = w["new_customers"] / w["new_customers"].sum()
        blended_cac = w["loaded_spend"].sum() / w["new_customers"].sum()
        exp_ltv = (share * summary.loc[PAID_CHANNELS, "ltv_12m"]).sum()
        rows.append({"period": name, "tiktok_share": share["TikTok Ads"], "blended_cac": blended_cac,
                     "expected_ltv_12m": exp_ltv, "expected_ltv_cac": exp_ltv / blended_cac})
    return pd.DataFrame(rows).set_index("period")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def main():
    plans = load_plans()
    cust, n_cust_raw = load_customers()
    orders, n_orders_raw = load_orders(plans)
    spend = load_spend()

    # Data quality check (same as 04_data_quality_checks.sql)
    print(f"customers: {n_cust_raw} raw rows -> {len(cust)} unique | unparsed dates "
          f"{cust['signup_date'].isna().sum()} | unmapped sources {(cust['channel'] == 'Other').sum()}")
    print(f"orders: {n_orders_raw} raw rows -> {len(orders)} valid | unparsed dates "
          f"{orders['order_date'].isna().sum()} | missing plan cost {orders['cogs_per_box'].isna().sum()}"
          f" | orphan orders {(~orders['customer_id'].isin(cust['customer_id'])).sum()}")
    print(f"spend: {len(spend)} rows | unparsed months {spend['month'].isna().sum()}"
          f" | unparsed amounts {spend['amount'].isna().sum()}")

    cohorts = build_cohorts(cust, orders)
    cac = cac_by_cohort(cust, spend)
    summary, curves = channel_summary(cust, cohorts, cac)

    pd.set_option("display.width", 200)
    print("\nChannel summary, cohorts Oct 2024 - Sep 2025 (12 full months each)\n")
    print(summary.round(2).to_string(), "\n")

    chart_retention_heatmap(cohorts)
    chart_retention_curves(curves)
    chart_payback(curves, summary)
    chart_cac_vs_ltv(summary)

    print("\nBudget shift into TikTok (expected LTV uses each channel's 12-month LTV above)\n")
    print(mix_shift(cust, cac, summary).round(2).to_string())

    print("\nBlack Friday cohorts vs other cohorts (cohorts with 6+ months of data)\n")
    print(promo_cohorts(cohorts).round(2).to_string())


if __name__ == "__main__":
    main()
