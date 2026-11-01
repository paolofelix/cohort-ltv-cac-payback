"""
Cohort LTV & CAC Payback - interactive dashboard (Streamlit).

Rebuilds the same pipeline as python/cohort_ltv_charts.py from the CSVs in /data
(dedupe, contribution margin, cohort grid, CAC, payback), then lets the viewer
switch CAC basis, LTV horizon, channels and cohorts.

Run locally:  streamlit run dashboard/app.py      (from the repo root)
"""

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Settings (same constants as the SQL and Python files)
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPO_URL = "https://github.com/paolofelix/cohort-ltv-cac-payback"

DATA_END_MONTH = pd.Timestamp("2026-09-01")
PAYMENT_FEE_RATE = 0.03
LTV_COHORTS = (pd.Timestamp("2024-10-01"), pd.Timestamp("2025-09-01"))
PAID_CHANNELS = ["Google Ads", "Meta Ads", "TikTok Ads"]
ALL_CHANNELS = PAID_CHANNELS + ["Organic / Direct", "Referral"]
CHANNEL_COLORS = {"Google Ads": "#2a78d6", "Meta Ads": "#eb6834", "TikTok Ads": "#1baf7a",
                  "Organic / Direct": "#eda100", "Referral": "#e87ba4"}

st.set_page_config(page_title="Cohort LTV & CAC Payback", page_icon="☕", layout="wide")


# ---------------------------------------------------------------------------
# 1. Load and clean (same rules as sql/02_udfs.sql and sql/03_staging.sql)
# ---------------------------------------------------------------------------
def to_num(series):
    cleaned = series.fillna("").astype(str).str.replace(r"[,$\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def to_date(series):
    s = series.astype(str).str.strip().str.slice(0, 10).str.replace(r"T.*$", "", regex=True)
    return pd.to_datetime(s, format="mixed", errors="coerce").dt.normalize()


def to_month(series):
    s = series.astype(str).str.strip()
    iso = pd.to_datetime(s + "-01", format="%Y-%m-%d", errors="coerce")
    mon = pd.to_datetime(s, format="%b-%Y", errors="coerce")
    us = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")
    return iso.fillna(mon).fillna(us).dt.to_period("M").dt.to_timestamp()


def map_channel(source, medium):
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


def months_between(later, earlier):
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


@st.cache_data
def load_all():
    p = pd.read_csv(DATA_DIR / "plans.csv", dtype=str)
    plans = pd.DataFrame({"plan_id": p["plan_id"].str.strip().str.upper(),
                          "cogs_per_box": to_num(p["cogs_per_box"]),
                          "shipping_per_box": to_num(p["shipping_per_box"])})

    raw_c = pd.read_csv(DATA_DIR / "customers.csv", dtype=str, keep_default_na=False)
    cust = pd.DataFrame({
        "customer_id": raw_c["customer_id"].str.strip().str.upper(),
        "signup_date": to_date(raw_c["signup_date"]),
        "channel": [map_channel(s, m) for s, m in zip(raw_c["utm_source"], raw_c["utm_medium"])],
        "source_medium": (raw_c["utm_source"].str.strip() + " / " + raw_c["utm_medium"].str.strip())
                         .replace(" / ", "(blank) / (blank)"),
    })
    cust = cust.sort_values(["customer_id", "signup_date"]).drop_duplicates("customer_id")
    cust["cohort_month"] = cust["signup_date"].dt.to_period("M").dt.to_timestamp()

    raw_o = pd.read_csv(DATA_DIR / "orders.csv", dtype=str, keep_default_na=False)
    status = raw_o["status"].str.strip().str.lower()
    o = pd.DataFrame({
        "order_id": raw_o["order_id"].str.strip(),
        "customer_id": raw_o["customer_id"].str.strip().str.upper(),
        "order_date": to_date(raw_o["order_date"]),
        "plan_id": raw_o["plan_id"].str.strip().str.upper(),
        "gross_amount": to_num(raw_o["gross_amount"]),
        "discount": to_num(raw_o["discount"]).fillna(0),
        "status": np.select([status.str.startswith("complete"), status.str.startswith("refund"),
                             status.str.startswith("cancel")],
                            ["completed", "refunded", "cancelled"], "unknown"),
    })
    dq = {"customers_raw": len(raw_c), "customers_clean": len(cust), "orders_raw": len(o)}
    test = o["customer_id"].str.startswith("TEST")
    dq["test_orders"] = int(test.sum())
    o = o[~test]
    before = len(o)
    o = o.drop_duplicates("order_id")
    dq["duplicate_orders"] = before - len(o)
    dq["cancelled_orders"] = int((o["status"] == "cancelled").sum())
    o = o[o["status"] != "cancelled"]
    o = o.merge(plans, on="plan_id", how="left")
    o["net_revenue"] = np.where(o["status"] == "refunded", 0.0, o["gross_amount"] - o["discount"])
    o["contribution"] = (o["net_revenue"] - o["cogs_per_box"] - o["shipping_per_box"]
                         - o["net_revenue"] * PAYMENT_FEE_RATE)
    o["order_month"] = o["order_date"].dt.to_period("M").dt.to_timestamp()
    dq["orders_clean"] = len(o)

    raw_s = pd.read_csv(DATA_DIR / "marketing_spend.csv", dtype=str, keep_default_na=False)
    spend = pd.DataFrame({"month": to_month(raw_s["month"]),
                          "channel": raw_s["channel"].map(map_ledger_channel),
                          "channel_raw": raw_s["channel"].str.strip(),
                          "category": raw_s["category"].str.strip(),
                          "amount": to_num(raw_s["amount"])})

    # Cohort grid: cohort_month x channel x month_number, zero-filled
    sizes = cust.groupby(["cohort_month", "channel"]).size().rename("cohort_size").reset_index()
    oc = o.merge(cust[["customer_id", "cohort_month", "channel"]], on="customer_id", how="inner")
    oc["month_number"] = months_between(oc["order_month"], oc["cohort_month"])
    activity = (oc.groupby(["cohort_month", "channel", "month_number"])
                  .agg(active_customers=("customer_id", "nunique"),
                       net_revenue=("net_revenue", "sum"),
                       contribution=("contribution", "sum")).reset_index())
    grid = sizes.copy()
    grid["max_month"] = months_between(pd.Series([DATA_END_MONTH] * len(grid)), grid["cohort_month"])
    grid = grid.loc[grid.index.repeat(grid["max_month"] + 1)]
    grid["month_number"] = grid.groupby(["cohort_month", "channel"]).cumcount()
    cohorts = grid.merge(activity, on=["cohort_month", "channel", "month_number"], how="left").fillna(
        {"active_customers": 0, "net_revenue": 0, "contribution": 0}).drop(columns="max_month")

    # CAC inputs by month and channel
    acq = spend[spend["channel"].isin(PAID_CHANNELS)]
    media = acq[acq["category"] == "Media"].groupby(["month", "channel"])["amount"].sum().rename("media_spend")
    loaded = acq.groupby(["month", "channel"])["amount"].sum().rename("loaded_spend")
    new = cust.groupby(["cohort_month", "channel"]).size().rename("new_customers")
    new.index = new.index.set_names(["month", "channel"])
    cac = pd.concat([new, media, loaded], axis=1).fillna(0).reset_index()
    return cust, o, spend, cohorts, cac, dq


def ltv_curves(cohorts, start, end):
    c = cohorts[cohorts["cohort_month"].between(start, end) & (cohorts["month_number"] <= 11)]
    by = c.groupby(["channel", "month_number"]).agg(
        contribution=("contribution", "sum"), active=("active_customers", "sum")).reset_index()
    size = c[c["month_number"] == 0].groupby("channel")["cohort_size"].sum()
    by["cohort_size"] = by["channel"].map(size)
    by["retention"] = by["active"] / by["cohort_size"]
    by["ltv"] = by.groupby("channel")["contribution"].cumsum() / by["cohort_size"]
    by["months_of_orders"] = by["month_number"] + 1
    return by


def summarise(curves, cac, start, end):
    agg = cac[cac["month"].between(start, end)].groupby("channel")[
        ["new_customers", "media_spend", "loaded_spend"]].sum()
    rows = []
    for ch in ALL_CHANNELS:
        cv = curves[curves["channel"] == ch].set_index("month_number")
        if cv.empty:
            continue
        r = {"channel": ch, "customers": int(cv["cohort_size"].iloc[0]),
             "m1_retention": cv.loc[1, "retention"], "m6_retention": cv.loc[6, "retention"],
             "ltv_3m": cv.loc[2, "ltv"], "ltv_6m": cv.loc[5, "ltv"], "ltv_12m": cv.loc[11, "ltv"]}
        if ch in PAID_CHANNELS and ch in agg.index:
            r["media_cac"] = agg.loc[ch, "media_spend"] / agg.loc[ch, "new_customers"]
            r["loaded_cac"] = agg.loc[ch, "loaded_spend"] / agg.loc[ch, "new_customers"]
        rows.append(r)
    return pd.DataFrame(rows).set_index("channel")


def payback_month(curve, cac_value):
    hit = curve.loc[curve["ltv"] >= cac_value, "months_of_orders"]
    return int(hit.min()) if len(hit) else None


def money(v):
    return "–" if pd.isna(v) else f"${v:,.0f}"


cust, orders, spend, cohorts, cac, dq = load_all()

# ---------------------------------------------------------------------------
# 2. Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("Controls")
cac_basis = st.sidebar.radio("CAC basis", ["Fully loaded", "Media only"],
                             help="Fully loaded adds agency fees and creative production "
                                  "(e.g. TikTok UGC creators). Software costs are excluded.")
cac_col = "loaded_cac" if cac_basis == "Fully loaded" else "media_cac"
horizon = st.sidebar.select_slider("LTV horizon (months of orders)", options=[3, 6, 12], value=12)
ltv_col = {3: "ltv_3m", 6: "ltv_6m", 12: "ltv_12m"}[horizon]
channels = st.sidebar.multiselect("Channels shown", ALL_CHANNELS, default=ALL_CHANNELS)
if not channels:
    st.sidebar.warning("Pick at least one channel.")
    channels = ALL_CHANNELS
paid_shown = [c for c in PAID_CHANNELS if c in channels]

st.sidebar.markdown("---")
st.sidebar.caption("LTV and payback use the Oct 2024 – Sep 2025 cohorts, the ones with 12 full "
                   "months of orders, so every channel is measured over the same window.")
st.sidebar.markdown(f"[GitHub repo]({REPO_URL}) · [Findings]({REPO_URL}/blob/main/FINDINGS.md)")

curves = ltv_curves(cohorts, *LTV_COHORTS)
summary = summarise(curves, cac, *LTV_COHORTS)
summary["ltv_cac"] = summary[ltv_col] / summary[cac_col]
for ch in PAID_CHANNELS:
    summary.loc[ch, "payback"] = payback_month(curves[curves["channel"] == ch], summary.loc[ch, cac_col])

# ---------------------------------------------------------------------------
# 3. Header and KPIs
# ---------------------------------------------------------------------------
st.title("Cohort LTV & CAC Payback by Channel")
st.markdown("Which channel's customers actually pay for themselves? A fictional DTC coffee "
            "subscription, Oct 2024 – Sep 2026. LTV is measured on **contribution margin** "
            "(after product, shipping and payment costs), so it compares directly with CAC. "
            "*Synthetic data.*")

paid_window = cac[cac["month"].between(*LTV_COHORTS) & cac["channel"].isin(PAID_CHANNELS)]
spend_key = "loaded_spend" if cac_basis == "Fully loaded" else "media_spend"
blended_cac = paid_window[spend_key].sum() / paid_window["new_customers"].sum()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Customers (deduplicated)", f"{len(cust):,}")
k2.metric("Valid orders", f"{len(orders):,}")
k3.metric(f"Blended paid CAC ({cac_basis.lower()})", money(blended_cac),
          help="Oct 2024 – Sep 2025 signups")
best = summary.loc[PAID_CHANNELS, "ltv_cac"].idxmax()
k4.metric(f"Best paid channel on {horizon}-month LTV:CAC", best,
          delta=f"{summary.loc[best, 'ltv_cac']:.2f}x", delta_color="off", delta_arrow="off")

tabs = st.tabs(["CAC vs LTV", "Payback curves", "Retention", "Max CAC calculator",
                "Budget shift", "Black Friday cohorts", "Data quality"])

# ---------------------------------------------------------------------------
# Tab: CAC vs LTV
# ---------------------------------------------------------------------------
with tabs[0]:
    st.subheader(f"What a customer costs vs what they return ({horizon} months)")
    if paid_shown:
        bars = summary.loc[paid_shown, ["media_cac", "loaded_cac", ltv_col]].reset_index().melt(
            id_vars="channel", var_name="measure", value_name="value")
        names = {"media_cac": "Media CAC", "loaded_cac": "Fully loaded CAC",
                 ltv_col: f"{horizon}-month LTV (contribution)"}
        bars["measure"] = bars["measure"].map(names)
        fig = px.bar(bars, x="channel", y="value", color="measure", barmode="group",
                     color_discrete_sequence=["#b7cdea", "#6f9bd6", "#1f4f8f"], text_auto="$,.0f")
        fig.update_traces(textposition="outside", marker_line_color="white", marker_line_width=2,
                          hovertemplate="%{x}<br>%{fullData.name}: $%{y:,.0f}<extra></extra>")
        for ch in paid_shown:
            fig.add_annotation(x=ch, y=bars["value"].max() * 1.18, showarrow=False,
                               text=f"<b>LTV:CAC {summary.loc[ch, 'ltv_cac']:.2f}</b>")
        fig.update_layout(height=430, xaxis_title=None, yaxis_title="$ per acquired customer",
                          yaxis_range=[0, bars["value"].max() * 1.25],
                          legend=dict(orientation="h", y=-0.12, title=None), margin=dict(t=30))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Select at least one paid channel in the sidebar.")

    st.subheader("Channel summary")
    tbl = summary.loc[channels].rename(columns={
        "customers": "Customers", "m1_retention": "Month-1 retention", "m6_retention": "Month-6 retention",
        "ltv_3m": "3-mo LTV", "ltv_6m": "6-mo LTV", "ltv_12m": "12-mo LTV", "media_cac": "Media CAC",
        "loaded_cac": "Fully loaded CAC", "ltv_cac": f"LTV:CAC ({horizon} mo, {cac_basis.lower()})",
        "payback": "Payback month"})
    st.dataframe(tbl.style.format({
        "Month-1 retention": "{:.0%}", "Month-6 retention": "{:.0%}", "3-mo LTV": "${:,.0f}",
        "6-mo LTV": "${:,.0f}", "12-mo LTV": "${:,.0f}", "Media CAC": "${:,.0f}",
        "Fully loaded CAC": "${:,.0f}", f"LTV:CAC ({horizon} mo, {cac_basis.lower()})": "{:.2f}",
        "Payback month": "{:.0f}"}, na_rep="–"), width="stretch")
    st.info("**Read-out:** ranked by CAC, TikTok looks best and Google worst. Ranked by what a "
            "customer returns, it is the other way round. Organic and Referral have no CAC "
            "because no media is bought for them.")

# ---------------------------------------------------------------------------
# Tab: Payback curves
# ---------------------------------------------------------------------------
with tabs[1]:
    st.subheader(f"Cumulative contribution per customer vs {cac_basis.lower()} CAC")
    st.caption("Solid line = cumulative contribution per acquired customer. Dashed line = CAC. "
               "Dot = the month the line crosses CAC (payback).")
    fig = go.Figure()
    for ch in channels:
        cv = curves[curves["channel"] == ch]
        fig.add_trace(go.Scatter(x=cv["months_of_orders"], y=cv["ltv"], name=ch, mode="lines",
                                 line=dict(color=CHANNEL_COLORS[ch], width=2),
                                 hovertemplate=f"{ch}: $%{{y:,.0f}}<extra></extra>"))
        if ch in PAID_CHANNELS:
            cac_v = summary.loc[ch, cac_col]
            fig.add_trace(go.Scatter(x=[1, 12], y=[cac_v, cac_v], mode="lines", showlegend=False,
                                     line=dict(color=CHANNEL_COLORS[ch], width=1, dash="dash"),
                                     hovertemplate=f"{ch} CAC: ${cac_v:,.0f}<extra></extra>"))
            pb = summary.loc[ch, "payback"]
            if pd.notna(pb):
                y = cv.loc[cv["months_of_orders"] == pb, "ltv"].iloc[0]
                fig.add_trace(go.Scatter(x=[pb], y=[y], mode="markers", showlegend=False,
                                         marker=dict(size=12, color=CHANNEL_COLORS[ch],
                                                     line=dict(color="white", width=2)),
                                         hovertemplate=f"{ch} pays back in month {int(pb)}<extra></extra>"))
    fig.update_layout(height=460, hovermode="x unified", xaxis=dict(title="Months of orders since signup",
                      dtick=1), yaxis=dict(title="$ per acquired customer", tickprefix="$"),
                      legend=dict(orientation="h", y=-0.15), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    cols = st.columns(max(len(paid_shown), 1))
    for col, ch in zip(cols, paid_shown):
        pb = summary.loc[ch, "payback"]
        col.metric(f"{ch} payback", f"Month {int(pb)}" if pd.notna(pb) else "Not in 12 months",
                   help=f"{cac_basis} CAC {money(summary.loc[ch, cac_col])}")

# ---------------------------------------------------------------------------
# Tab: Retention
# ---------------------------------------------------------------------------
with tabs[2]:
    st.subheader("Share of customers still ordering, by months since signup")
    rc = curves[curves["channel"].isin(channels)]
    fig = px.line(rc, x="month_number", y="retention", color="channel",
                  color_discrete_map=CHANNEL_COLORS, markers=True)
    fig.update_traces(line_width=2, hovertemplate="%{fullData.name}: %{y:.0%}<extra></extra>")
    fig.update_layout(height=400, hovermode="x unified", yaxis_tickformat=".0%", yaxis_range=[0, 1.05],
                      xaxis=dict(title="Months since signup (0 = first box)", dtick=1),
                      yaxis_title="Still ordering", legend=dict(orientation="h", y=-0.18, title=None),
                      margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Cohort heatmap")
    h1, h2 = st.columns([1, 1])
    hm_channel = h1.selectbox("Cohorts from", ["All channels"] + ALL_CHANNELS)
    hm_metric = h2.radio("Show", ["Retention", "Cumulative LTV per customer"], horizontal=True)
    hc = cohorts if hm_channel == "All channels" else cohorts[cohorts["channel"] == hm_channel]
    hc = hc[hc["month_number"] <= 12].groupby(["cohort_month", "month_number"])[
        ["active_customers", "cohort_size", "contribution"]].sum().reset_index()
    if hm_metric == "Retention":
        hc["value"] = hc["active_customers"] / hc["cohort_size"]
        fmt, scale, zmax = ".0%", "Blues", 1
    else:
        hc["value"] = hc.groupby("cohort_month")["contribution"].cumsum() / hc["cohort_size"]
        fmt, scale, zmax = "$,.0f", "Blues", None
    grid = hc.pivot(index="cohort_month", columns="month_number", values="value")
    grid.index = grid.index.strftime("%b %Y")
    fig = px.imshow(grid, color_continuous_scale=scale, aspect="auto", text_auto=fmt,
                    zmin=0, zmax=zmax, labels=dict(x="Months since signup", y="Signup cohort", color=hm_metric))
    fig.update_traces(hovertemplate="Cohort %{y}<br>Month %{x}: %{z:" + fmt.replace("$", "") + "}<extra></extra>")
    fig.update_layout(height=720, xaxis=dict(dtick=1), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    st.caption("Blank cells: the cohort hasn't reached that month yet. November rows are the "
               "Black Friday cohorts (50% off the first box).")

# ---------------------------------------------------------------------------
# Tab: Max CAC calculator
# ---------------------------------------------------------------------------
with tabs[3]:
    st.subheader("How much can each channel pay for a customer?")
    st.markdown("Pick the payback period finance will accept. The maximum CAC for each channel is "
                "the contribution its customers return within that period.")
    rule = st.slider("Required payback within (months of orders)", 1, 12, 12)
    calc = curves[curves["months_of_orders"] == rule].set_index("channel")["ltv"].rename("max_cac")
    out = pd.DataFrame({"Max CAC": calc}).loc[[c for c in PAID_CHANNELS if c in calc.index]]
    out["Current CAC"] = summary.loc[out.index, cac_col]
    out["Headroom per customer"] = out["Max CAC"] - out["Current CAC"]
    out["Verdict"] = np.where(out["Headroom per customer"] >= 0, "✅ Pays back", "❌ Too expensive")

    fig = go.Figure()
    fig.add_trace(go.Bar(x=out.index, y=out["Current CAC"], name=f"Current CAC ({cac_basis.lower()})",
                         marker_color="#b7cdea", marker_line_color="white", marker_line_width=2,
                         text=[money(v) for v in out["Current CAC"]], textposition="outside"))
    fig.add_trace(go.Bar(x=out.index, y=out["Max CAC"], name=f"Max CAC for {rule}-month payback",
                         marker_color="#1f4f8f", marker_line_color="white", marker_line_width=2,
                         text=[money(v) for v in out["Max CAC"]], textposition="outside"))
    fig.update_layout(barmode="group", height=380, yaxis=dict(title="$ per customer", tickprefix="$"),
                      legend=dict(orientation="h", y=-0.15), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(out.style.format({"Max CAC": "${:,.0f}", "Current CAC": "${:,.0f}",
                                   "Headroom per customer": "${:+,.0f}"}), width="stretch")

# ---------------------------------------------------------------------------
# Tab: Budget shift
# ---------------------------------------------------------------------------
with tabs[4]:
    st.subheader("The budget moved into TikTok: CAC fell, value fell faster")
    m = cac[cac["channel"].isin(PAID_CHANNELS)].copy()
    m = m[m["month"] <= DATA_END_MONTH]
    tot = m.groupby("month")[["new_customers", "loaded_spend", "media_spend"]].sum()
    mix = m.pivot(index="month", columns="channel", values="new_customers").fillna(0)
    share = mix.div(mix.sum(axis=1), axis=0)
    exp_ltv = (share * summary.loc[PAID_CHANNELS, ltv_col]).sum(axis=1)
    monthly = pd.DataFrame({"Blended CAC": tot[spend_key] / tot["new_customers"],
                            f"Expected {horizon}-mo LTV": exp_ltv})

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Mix of paid signups**")
        sl = share.reset_index().melt(id_vars="month", var_name="channel", value_name="share")
        fig = px.area(sl, x="month", y="share", color="channel", color_discrete_map=CHANNEL_COLORS)
        fig.update_traces(line_width=0.5, hovertemplate="%{fullData.name}: %{y:.0%}<extra></extra>")
        fig.update_layout(height=360, hovermode="x unified", yaxis_tickformat=".0%", xaxis_title=None,
                          yaxis_title="Share of paid signups", legend=dict(orientation="h", y=-0.2, title=None),
                          margin=dict(t=10))
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown(f"**Blended CAC vs expected {horizon}-month LTV per paid customer**")
        ml = monthly.reset_index().melt(id_vars="month", var_name="measure", value_name="value")
        fig = px.line(ml, x="month", y="value", color="measure",
                      color_discrete_map={"Blended CAC": "#b7cdea", f"Expected {horizon}-mo LTV": "#1f4f8f"})
        fig.update_traces(line_width=2, hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>")
        fig.update_layout(height=360, hovermode="x unified", xaxis_title=None,
                          yaxis=dict(title="$ per paid customer", tickprefix="$"),
                          legend=dict(orientation="h", y=-0.2, title=None), margin=dict(t=10))
        st.plotly_chart(fig, width="stretch")
    st.caption("Expected LTV weights each channel's mature LTV (Oct 2024 – Sep 2025 cohorts) by its "
               "share of signups that month, because recent cohorts are too young to measure directly. "
               "Both lines are in the same unit ($ per customer), so they share one axis.")

    periods = {"Oct 2024 – Mar 2025": ("2024-10-01", "2025-03-01"),
               "Apr 2026 – Sep 2026": ("2026-04-01", "2026-09-01")}
    rows = []
    for name, (a, b) in periods.items():
        w = m[m["month"].between(a, b)].groupby("channel")[["new_customers", spend_key]].sum()
        s = w["new_customers"] / w["new_customers"].sum()
        bc = w[spend_key].sum() / w["new_customers"].sum()
        el = (s * summary.loc[PAID_CHANNELS, ltv_col]).sum()
        rows.append({"Period": name, "TikTok share of paid signups": s["TikTok Ads"], "Blended CAC": bc,
                     f"Expected {horizon}-mo LTV": el, "LTV:CAC": el / bc})
    st.dataframe(pd.DataFrame(rows).set_index("Period").style.format({
        "TikTok share of paid signups": "{:.0%}", "Blended CAC": "${:,.0f}",
        f"Expected {horizon}-mo LTV": "${:,.0f}", "LTV:CAC": "{:.2f}"}), width="stretch")

# ---------------------------------------------------------------------------
# Tab: Black Friday cohorts
# ---------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Black Friday (50% off first box) vs other cohorts")
    c = cohorts[cohorts["cohort_month"] <= DATA_END_MONTH - pd.DateOffset(months=6)].copy()
    c = c[c["channel"].isin(channels)]
    c["cohort_type"] = np.where(c["cohort_month"].dt.month == 11, "Black Friday (Nov)", "Other months")
    size = c[c["month_number"] == 0].groupby("cohort_type")["cohort_size"].sum()
    act = c[c["month_number"] <= 6].groupby(["cohort_type", "month_number"])["active_customers"].sum().reset_index()
    act["retention"] = act["active_customers"] / act["cohort_type"].map(size)
    contrib6 = c[c["month_number"] <= 5].groupby("cohort_type")["contribution"].sum()
    signups_per_month = c[c["month_number"] == 0].groupby(["cohort_type", "cohort_month"])["cohort_size"].sum() \
        .groupby("cohort_type").mean()

    k = st.columns(3)
    k[0].metric("Signups per cohort month", f"{signups_per_month['Black Friday (Nov)']:,.0f}",
                delta=f"{signups_per_month['Black Friday (Nov)'] / signups_per_month['Other months']:.1f}x other months",
                delta_color="off", delta_arrow="off")
    k[1].metric("6-month LTV, Black Friday", money(contrib6["Black Friday (Nov)"] / size["Black Friday (Nov)"]))
    k[2].metric("6-month LTV, other months", money(contrib6["Other months"] / size["Other months"]))

    fig = px.line(act, x="month_number", y="retention", color="cohort_type", markers=True,
                  color_discrete_map={"Black Friday (Nov)": "#eb6834", "Other months": "#2a78d6"})
    fig.update_traces(line_width=2, hovertemplate="%{fullData.name}: %{y:.0%}<extra></extra>")
    fig.update_layout(height=380, hovermode="x unified", yaxis_tickformat=".0%", yaxis_range=[0, 1.05],
                      xaxis=dict(title="Months since signup", dtick=1), yaxis_title="Still ordering",
                      legend=dict(orientation="h", y=-0.2, title=None), margin=dict(t=20))
    st.plotly_chart(fig, width="stretch")
    st.caption("Cohorts with at least 6 months of data. Respects the channel filter in the sidebar.")

# ---------------------------------------------------------------------------
# Tab: Data quality
# ---------------------------------------------------------------------------
with tabs[6]:
    st.subheader("What the cleaning step fixed")
    checks = pd.DataFrame([
        ("Customer rows: raw → unique", f"{dq['customers_raw']:,} → {dq['customers_clean']:,}"),
        ("Order rows: raw → valid", f"{dq['orders_raw']:,} → {dq['orders_clean']:,}"),
        ("Test orders removed", f"{dq['test_orders']:,}"),
        ("Duplicate order rows removed", f"{dq['duplicate_orders']:,}"),
        ("Cancelled orders removed", f"{dq['cancelled_orders']:,}"),
        ("Unparsed signup / order dates", f"{int(cust['signup_date'].isna().sum() + orders['order_date'].isna().sum())}"),
        ("Unmapped customer sources", f"{int((cust['channel'] == 'Other').sum())}"),
        ("Unparsed ledger months", f"{int(spend['month'].isna().sum())}"),
    ], columns=["Check", "Result"])
    st.dataframe(checks, width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**UTM source / medium labels mapped to each channel**")
        lab = cust.groupby(["channel", "source_medium"]).size().rename("customers").reset_index()
        st.dataframe(lab.sort_values(["channel", "customers"], ascending=[True, False]),
                     width="stretch", hide_index=True, height=420)
    with c2:
        st.markdown("**Ledger spellings mapped to each channel**")
        led = spend.groupby(["channel", "channel_raw"]).size().rename("rows").reset_index()
        st.dataframe(led, width="stretch", hide_index=True, height=420)

with st.expander("About this dashboard"):
    st.markdown(f"""
- **Data:** synthetic customers, orders, finance ledger and plan costs for a fictional coffee
  subscription, built with the problems of real exports (duplicate customers, 22 source/medium
  labels, test and cancelled orders, three ledger month formats).
- **Contribution margin:** net revenue − cost of goods − shipping − 3% payment fees. Refunded boxes
  keep their cost but earn nothing.
- **Payback month:** the first month in which cumulative contribution per customer covers CAC.
- **Pipeline:** the same logic as the BigQuery SQL in `/sql` and the pandas script in `/python`.
- **Code:** [{REPO_URL}]({REPO_URL})
""")
