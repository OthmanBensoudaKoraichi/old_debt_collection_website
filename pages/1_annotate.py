# pages/1_annotate.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import streamlit as st
from streamlit_scroll_to_top import scroll_to_here


# ─────────────────────────────────────────────────────────────────────────────
# PAGE SETUP & BASIC GUARDS
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(initial_sidebar_state="expanded", layout="wide")

if "supabase" not in st.session_state:
    st.switch_page("app.py")
if "annotator_id" not in st.session_state:
    st.switch_page("app.py")


supabase = st.session_state["supabase"]
annotator_id = st.session_state["annotator_id"]


direct_mode = st.session_state.get("direct_case_mode", False)
direct_case_number = st.session_state.get("direct_case_number")

YES_NO_UNSURE = ["Yes", "No", "Unclear"]
YES_NO_NA_UNSURE = ["Yes", "No", "N/A", "Unclear"]
YES_NO_ONLY12_UNSURE = ["Yes", "No", "Only 1–2 owners", "Unclear"]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def now_utc_iso_z() -> str:
    """
    Current UTC in ISO8601 with 'Z' suffix (timezone-aware; no deprecation warning).
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_iso_to_utc(dt_str: str) -> datetime:
    """
    Parse an ISO8601 string (possibly with 'Z' or date-only) to a timezone-aware UTC datetime.
    Defensive against bad inputs; falls back to 'now' (UTC).
    """
    if not isinstance(dt_str, str):
        return datetime.now(timezone.utc)

    s = dt_str.strip().replace("Z", "+00:00")

    # Try full datetime first
    try:
        d = datetime.fromisoformat(s)
    except Exception:
        # If it's likely a date-only string like '2024-03-01', append midnight UTC
        try:
            d = datetime.fromisoformat(s + "T00:00:00+00:00")
        except Exception:
            return datetime.now(timezone.utc)

    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)

def fmt_date(d) -> str:
    """
    Display helper for dates as MM/DD/YYYY (US format); em dash if falsy.
    Accepts ISO strings, date-only strings, or datetime/date objects.
    """
    if not d:
        return "—"
    try:
        # Normalize to aware UTC datetime using the parser above
        if isinstance(d, datetime):
            dt = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        else:
            dt = parse_iso_to_utc(str(d))
        return dt.strftime("%m/%d/%Y")
    except Exception:
        # Fall back to raw string if something unexpected comes through
        return str(d)

def fmt_party_list(v: Any) -> str:
    """
    Format list of parties as a comma-separated string; em dash if missing.
    """
    if isinstance(v, list):
        return ", ".join([str(x) for x in v if x]) or "—"
    return "—"

def normalize_case_row(r: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a case row to a stable dict with lowercase keys.
    All columns are assumed lowercase in Supabase.
    """
    return {
        "case_number": r.get("case_number"),
        "plaintiff": r.get("plaintiff") or [],
        "defendant": r.get("defendant") or [],
        "complaint_filed_date": r.get("complaint_filed_date"),
        "progress": r.get("progress") or "incomplete",
        "annotator_id": r.get("annotator_id"),
    }

def fetch_cases_for_annotator(annotator: str) -> List[Dict[str, Any]]:
    """
    Load all training cases for a given annotator_id (lowercase schema).
    """
    res = (
        supabase.table("training_cases_gold")
        .select("*")
        .ilike("annotator_id", annotator_id)
        .order("case_number", desc=False)
        .execute()
    )
    rows = res.data or []
    return [normalize_case_row(r) for r in rows if r.get("case_number")]

def fetch_case_by_number_for_annotator(annotator_id: str, case_number: str) -> Optional[Dict[str, Any]]:
    """
    Load a single case by case_number for the annotator (lowercase schema).
    """
    res = (
        supabase.table("training_cases_gold")
        .select("*")
        .eq("case_number", case_number)
        .ilike("annotator_id", annotator_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return normalize_case_row(rows[0]) if rows else None

def pick_next_incomplete(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Pick the first case whose progress != 'complete'.
    """
    for r in rows:
        if (r.get("progress") or "").lower() != "complete":
            return r
    return None

def fetch_case_pdfs(case_number: str) -> List[Dict[str, str]]:
    """
    Return [{name, link}] for case documents in gdrive_files.
    Columns in Supabase are lowercase (unless quoted), so select them in lowercase.
    Also tolerate unexpected casing by falling back to alternative keys.
    """
    try:
        res = (
            supabase.table("gdrive_files")
            .select("CASE_NUMBER, LINK_DRIVE, DOCUMENT_NAME_DRIVE")
            .eq("CASE_NUMBER", case_number)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        # Surface the error in the UI once so it's debuggable
        st.sidebar.caption(f"⚠️ Could not load Drive docs: {e}")
        rows = []

    out: List[Dict[str, str]] = []
    for r in rows:
        # Be defensive about key names just in case the table was created with quoted columns
        link = (r.get("link_drive")
                or r.get("LINK_DRIVE")
                or r.get("Link_Drive")
                or "").strip()
        name = (r.get("document_name_drive")
                or r.get("DOCUMENT_NAME_DRIVE")
                or r.get("Document_Name_Drive")
                or "Document").strip()
        if link:
            out.append({"name": name or "Document", "link": link})
    return out


def set_case_progress(case_number: str, status: str, annotator: str):
    """
    Update the progress field on training_cases_gold (draft/complete) for THIS annotator only.
    Scoping by (case_number, annotator_id) prevents collisions when multiple annotators share a case.
    """
    try:
        result = (
            supabase.table("training_cases_gold")
            .update({"progress": status})
            .eq("case_number", case_number)
            .ilike("annotator_id", annotator_id)
            .execute()
        )
        if not result.data:
            st.warning(f"Progress update affected 0 rows for case {case_number}")
    except Exception as e:
        st.error(f"Failed to update case progress: {e}")

def load_existing_result(case_number: str, annotator_id: str) -> Dict[str, Any]:
    """
    Load the most recent result row for (case_number, annotator_id) from training_results_gold.
    """
    res = (
        supabase.table("training_results_gold")
        .select("*")
        .eq("case_number", case_number)
        .eq("annotator_id", annotator_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return (res.data[0] if res.data else {}) or {}


def upsert_results_gold(case_number: str, annotator_id: str, payload: dict, is_final: bool = False):
    """
    - Autosave (is_final=False): overwrite existing version (no new version)
    - Manual resubmit (is_final=True): insert a new version
    """

    # Fetch latest version for this case + annotator
    res = (
        supabase.table("training_results_gold")
        .select("version")
        .eq("case_number", case_number)
        .eq("annotator_id", annotator_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )

    existing_row = res.data[0] if res.data else None
    last_version = existing_row["version"] if existing_row else 0

    # → Autosave = keep same version (or start at 1 if no row exists)
    # → Manual submit = increment version
    if is_final:
        version_to_use = last_version + 1
    else:
        version_to_use = last_version if last_version > 0 else 1

    row = payload.copy()
    row["case_number"] = case_number
    row["annotator_id"] = annotator_id
    row["version"] = version_to_use
    row["created_at"] = now_utc_iso_z()

    if is_final or not existing_row:
        # Manual resubmit OR first-time save → insert new row
        supabase.table("training_results_gold").insert(row).execute()
    else:
        # Autosave on existing row → update in place
        result = supabase.table("training_results_gold") \
            .update(row) \
            .eq("case_number", case_number) \
            .eq("annotator_id", annotator_id) \
            .eq("version", last_version) \
            .execute()
        
        # Fallback: if update affected 0 rows, insert instead
        if not result.data:
            supabase.table("training_results_gold").insert(row).execute()




def get_next_incomplete_or_draft_after(current_case_number: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Round-robin: from current case, find the next whose progress != 'complete'.
    """
    if not rows:
        return None
    idx = 0
    for i, r in enumerate(rows):
        rn = r.get("case_number")
        if str(rn) == str(current_case_number):
            idx = i
            break
    n = len(rows)
    for step in range(1, n + 1):
        j = (idx + step) % n
    # pick next not complete
        pr = (rows[j].get("progress") or "").lower()
        if pr != "complete":
            return normalize_case_row(rows[j])
    return None


def insert_fallback_snapshot(case_number: str, annotator_id: str, payload: Dict[str, Any]):
    # Get the latest version from training_results_gold to match
    res = (
        supabase.table("training_results_gold")
        .select("version")
        .eq("case_number", case_number)
        .eq("annotator_id", annotator_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    latest_version = res.data[0]["version"] if res.data else 1
    
    row = {
        "case_number": case_number,
        "annotator_id": annotator_id,
        "version": latest_version,
        "created_at": now_utc_iso_z(),
        **payload,
    }
    supabase.table("training_results_gold_fallback").insert(row).execute()

# If a previous action asked us to scroll on the next run, do it immediately
if st.session_state.pop("scroll_to_top", False):
    # Calling this at the very top will scroll the viewport to the top of the page
    scroll_to_here(0, key="top")

# ─────────────────────────────────────────────────────────────────────────────
# LOAD CURRENT CASE
# ─────────────────────────────────────────────────────────────────────────────
all_cases = fetch_cases_for_annotator(annotator_id)
total = len(all_cases)
done = sum(1 for r in all_cases if (r.get("progress") or "").lower() == "complete")
left = total - done

if direct_mode and direct_case_number:
    current = fetch_case_by_number_for_annotator(annotator_id, direct_case_number)
    if not current:
        st.error(f"Case '{direct_case_number}' not found or not assigned to '{annotator_id}'.")
        st.stop()
else:
    current = pick_next_incomplete(all_cases)

st.sidebar.markdown(f"**Annotator:** `{annotator_id}`")
st.sidebar.metric("Completed", done)
st.sidebar.metric("Remaining", left)
st.sidebar.metric("Total", total)
if total:
    st.sidebar.progress(min((done + 1) / max(1, total), 1.0), text=f"Case {min(done + 1, total)} of {total}")

if not total:
    st.info("No cases assigned to you.")
    st.stop()
if current is None:
    st.success("🎉 All cases completed. Great work!")
    st.info("Redirecting to completed cases in 2 seconds...")
    import time
    time.sleep(2)
    # Clear any scroll flags before switching
    st.session_state.pop("scroll_to_top", None)
    st.session_state.pop("direct_case_mode", None)
    st.session_state.pop("direct_case_number", None)
    st.switch_page("pages/2_cases_completed.py")

case_number = current["case_number"]
st.sidebar.markdown(f"**Case:** `{case_number}`")
st.sidebar.markdown("**Plaintiff(s):** " + fmt_party_list(current.get("plaintiff")))
st.sidebar.markdown("**Defendant(s):** " + fmt_party_list(current.get("defendant")))
st.sidebar.markdown("**Complaint Filed Date:** " + fmt_date(current.get("complaint_filed_date")))

files = fetch_case_pdfs(case_number)
if files:
    st.sidebar.markdown("**Google Drive Documents:**")
    for d in files:
        try:
            st.sidebar.link_button(d["name"], d["link"], use_container_width=True)
        except Exception:
            st.sidebar.markdown(f"- [{d['name']}]({d['link']})")
else:
    st.sidebar.caption("No documents found for this case.")
st.sidebar.caption("ℹ️ Note: Document names shown here correspond exactly to the file names in Google Drive.")

from datetime import timedelta

# ─────────────────────────────────────────────────────────────────────────────
# TIMER (persist + live display)
# ─────────────────────────────────────────────────────────────────────────────
if "case_elapsed_seconds" not in st.session_state:
    st.session_state["case_elapsed_seconds"] = {}

if case_number not in st.session_state["case_elapsed_seconds"]:
    try:
        res = (
            supabase.table("training_results_gold")
            .select("time_caselevel")
            .eq("case_number", case_number)
            .eq("annotator_id", annotator_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data = res.data[0] if res.data else None
        st.session_state["case_elapsed_seconds"][case_number] = float(data["time_caselevel"]) if (data and data.get("time_caselevel")) else 0.0
    except Exception:
        st.session_state["case_elapsed_seconds"][case_number] = 0.0

# Record the reference "open" time so we can accumulate live seconds
if "case_open_time" not in st.session_state:
    st.session_state["case_open_time"] = {}
if case_number not in st.session_state["case_open_time"]:
    # only set once per session; don't reset on rerun
    st.session_state["case_open_time"][case_number] = datetime.now(timezone.utc)


with st.sidebar:

    @st.fragment(run_every=1)
    def timer_fragment():
        base = st.session_state["case_elapsed_seconds"][case_number]
        opened_at = st.session_state["case_open_time"][case_number]
        now = datetime.now(timezone.utc)
        elapsed = base + (now - opened_at).total_seconds()
        st.session_state["elapsed_seconds_live"] = elapsed
        m, s = divmod(int(elapsed), 60)
    timer_fragment()


# ─────────────────────────────────────────────────────────────────────────────
# EXISTING ANSWERS + KEY NAMESPACE
# ─────────────────────────────────────────────────────────────────────────────
existing = load_existing_result(case_number, annotator_id)

def dget(k: str, default: Any = "") -> Any:
    """
    Safe getter from the 'existing' row; default if missing/None.
    """
    v = existing.get(k)
    return default if v is None else v

def K(name: str) -> str:
    """
    Namespace widget keys per case to avoid collisions across case switches.
    (Session-level isolation already prevents cross-user collisions.)
    """
    return f"{case_number}::{name}"

# ─────────────────────────────────────────────────────────────────────────────
# LIVE WIDGETS (no form)
# ─────────────────────────────────────────────────────────────────────────────
st.title("Debt Collection – Gold Label Annotation")
st.subheader(f"Case: {case_number}")

# 1) RFDJ amounts / complaint
st.markdown("### 1) Request for Default Judgment and Complaint – Amounts Sought")
st.text_input(
    "What is the dollar amount sought for the demand of the complaint in the request for default judgment?",
    dget("rfdj_demand_amount"), key=K("rfdj_demand_amount")
)
st.text_input(
    "What is the dollar amount sought for the interest in the request for default judgment?",
    dget("rfdj_interest_amount"), key=K("rfdj_interest_amount")
)
st.text_input(
    "What is the dollar amount sought for the cost in the request for default judgment?",
    dget("rfdj_cost_amount"), key=K("rfdj_cost_amount")
)
st.text_input(
    "What is the dollar amount sought for the attorney fees in the request for default judgment?",
    dget("rfdj_attorney_fees_amount"), key=K("rfdj_attorney_fees_amount")
)

st.radio(
    "Does the complaint include a prayer section?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("complaint_has_prayer", "Unclear")) if dget("complaint_has_prayer") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("complaint_has_prayer")
)

st.radio(
    'The dollar amounts sought in the request for default judgment for the "demand of the complaint" and "interest" must be equal to or less than those sought in the prayer of the complaint. If the request for default judgment includes any non-zero "costs" or "attorney fees," then the prayer of the complaint must also request costs of suit or attorney fees, respectively. However, a specific dollar amount is not required in the prayer of the complaint for the cost of suit and attorney fees.',
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("rfdjamount_1", "Unclear")) if dget("rfdjamount_1") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("rfdjamount_1")
)

# 2) Required allegations (radio + details)
st.markdown("### 2) Required allegations in the Complaint")
st.radio(
    "Is the plaintiff a debt buyer  (§ 1788.58(a)(1)) ?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("allegation_debt_buyer_1", "Unclear")) if dget("allegation_debt_buyer_1") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("allegation_debt_buyer_1")
)
st.radio(
    "Is there a short and plain statement alleging the nature of the debt (§ 1788.58(a)(2))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("allegation_nature_of_debt_2", "Unclear")) if dget("allegation_nature_of_debt_2") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("allegation_nature_of_debt_2")
)
st.radio(
    "Is the debt buyer the sole owner of the debt at issue, or has authority to assert the rights of all owners of the debt (§ 1788.58(a)(3))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("allegation_sole_owner_3", "Unclear")) if dget("allegation_sole_owner_3") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("allegation_sole_owner_3")
)

st.radio(
    "Does the complaint allege the debt balance at charge-off (§ 1788.58(a)(4))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_chargeoff_balance_4", "Unclear")) if dget("alleges_chargeoff_balance_4") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_chargeoff_balance_4")
)
st.text_input(
    "If yes, specify the alleged debt balance at charge-off",
    dget("alleged_chargeoff_balance_4"), key=K("alleged_chargeoff_balance_4")
)

st.radio(
    "Does the complaint allege the date of last payment (§ 1788.58(a)(5))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_last_payment_date_5", "Unclear")) if dget("alleges_last_payment_date_5") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_last_payment_date_5")
)
st.text_input(
    "If yes, specify the alleged date of last payment (MM/DD/YYYY)",
    dget("alleged_last_payment_date_5"), key=K("alleged_last_payment_date_5")
)

st.radio(
    "Does the complaint allege the date of default (§ 1788.58(a)(5))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_default_date_5b", "Unclear")) if dget("alleges_default_date_5b") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_default_date_5b")
)
st.text_input(
    "If yes, specify the alleged date of default (MM/DD/YYYY)",
    dget("alleged_default_date_5b"), key=K("alleged_default_date_5b")
)

st.radio(
    "Does the complaint allege the name and address of the charge-off creditor (§ 1788.58(a)(6))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_chargeoff_creditor_info_6", "Unclear")) if dget("alleges_chargeoff_creditor_info_6") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_chargeoff_creditor_info_6")
)
st.text_input(
    "If yes, specify the alleged name and address of the charge-off creditor",
    dget("alleged_chargeoff_creditor_info_6"), key=K("alleged_chargeoff_creditor_info_6")
)

st.radio(
    "Does the complaint allege the debtor’s last known address (§ 1788.58(a)(7))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_debtor_last_known_address_7", "Unclear")) if dget("alleges_debtor_last_known_address_7") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_debtor_last_known_address_7")
)
st.text_input(
    "If yes, specify the alleged last known address of the debtor",
    dget("alleged_debtor_last_known_address_7"), key=K("alleged_debtor_last_known_address_7")
)

st.radio(
    "Does the complaint allege the names of all post-charge-off purchasers (§ 1788.58(a)(8))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("alleges_postchargeoffpurchaserinfo_0", "Unclear")) if dget("alleges_postchargeoffpurchaserinfo_0") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("alleges_postchargeoffpurchaserinfo_0")
)
st.text_input(
    "If yes, list the alleged purchasers in order separated by semicolons",
    dget("postchargeoffpurchaserinfo_0"), key=K("postchargeoffpurchaserinfo_0")
)

st.radio(
    "Does the plaintiff allege that they complied with Section 1788.52?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("allegation_1788_52_compliance", "Unclear")) if dget("allegation_1788_52_compliance") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("allegation_1788_52_compliance")
)

# 3) Debtor’s agreement in Complaint
st.markdown("### 3) Debtor’s agreement to the debt in the Complaint")
st.radio(
    "Is there a contract or credit statement (with a payment or purchase transaction) proving the defendant's agreement to the debt in the exhibits attached to the Complaint?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("hascontractorlaststatement_0", "Unclear")) if dget("hascontractorlaststatement_0") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("hascontractorlaststatement_0")
)
st.text_input(
    "Please indicate the document ID and exhibit number where this proof exists. Otherwise, leave blank.",
    dget("complaint_agreement_ref"), key=K("complaint_agreement_ref")
)

# 4) Sworn declaration
st.markdown("### 4) Sworn 1788/585 declaration")
st.radio(
    "Is there a sworn declaration document filed in support of the request for default judgment (CA Civil Code § 1788.60 (a))?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("hasSignedSworn1788.60_0", "Unclear")) if dget("hasSignedSworn1788.60_0") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("hasSignedSworn1788.60_0")
)
st.radio(
    "Please indicate whether a declaration exists from the plaintiff or someone who works for the plaintiff that satisfies the personal knowledge requirement of the records in possession of the plaintiff.",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("declaration_pkm_present", "Unclear")) if dget("declaration_pkm_present") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("declaration_pkm_present")
)
st.text_input(
    "Please indicate which number this plaintiff debt buyer is in the chain of title, as evidenced by the exhibits attached to the declaration. The first debt buyer would be the second in the chain of title, as the original seller of the debt would be the first.",
    dget("chain_position_number"), key=K("chain_position_number")
)
st.radio(
    "If the debt buyer is the third or later in the chain of title, do declarations exist establishing personal knowledge of the business records of all debt buyers?",
    YES_NO_ONLY12_UNSURE,
    index=YES_NO_ONLY12_UNSURE.index(dget("declaration_prior_buyers_pkm", "Unclear")) if dget("declaration_prior_buyers_pkm") in YES_NO_ONLY12_UNSURE else 3,
    horizontal=True, key=K("declaration_prior_buyers_pkm")
)
st.text_input(
    "Please indicate the document ID(s) and exhibit number(s) for personal knowledge of prior buyers; separate entries with semicolons.",
    dget("declaration_pkm_refs"), key=K("declaration_pkm_refs")
)

# 5) Agreement in declaration
st.markdown("### 5) Debtor’s agreement to the debt in the declaration")
st.radio(
    "Is there a contract or credit statement (with a payment or purchase transaction) proving the defendant's agreement to the debt in the exhibits attached to the declaration?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("declaration_has_agreement_proof", "Unclear")) if dget("declaration_has_agreement_proof") in YES_NO_UNSURE else 2,
    horizontal=True, key=K("declaration_has_agreement_proof")
)
st.text_input(
    "(st.text) Please indicate the document ID and exhibit number where this proof exists.  For example, Bill of Sale, attached in Complaint, 22IWUD01199_87855599, at 17 (Exhibit A)). Otherwise, leave the field blank",
    dget("declaration_agreement_ref"), key=K("declaration_agreement_ref")
)

# 6) Ownership chain
st.markdown("### 6) Exhibits from the declaration substantiate ownership chain")

st.radio(
    "Do the bills of sale attached in the declaration show that the debt buyer plaintiff is the sole owner of the debt and obtained the debt through a chain of ownership linked to the original seller?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("ownership_chain_sufficient", "Unclear"))
        if dget("ownership_chain_sufficient") in YES_NO_UNSURE else 2,
    horizontal=True,
    key=K("ownership_chain_sufficient"),
)

st.radio(
    "Do all of the bills of sale specify the account number in question?",
    YES_NO_UNSURE,
    index=YES_NO_UNSURE.index(dget("ownership_chain_account_match", "Unclear"))
        if dget("ownership_chain_account_match") in YES_NO_UNSURE else 2,
    horizontal=True,
    key=K("ownership_chain_account_match"),
)


st.text_input(
    "Please indicate the document ID and exhibit number where this proof exists.  For example, Bill of Sale, attached in the Declaration, 22IWUD01199_87855599, at 17 (Exhibit A)).  Please separate entries for different pieces of evidence with semicolons.",
    dget("ownership_chain_refs"), key=K("ownership_chain_refs")
)

# ─────────────────────────────────────────────────────────────────────────────
# 7–11) “You indicated…” block – live-updating via fragment
# ─────────────────────────────────────────────────────────────────────────────
@st.fragment(run_every=1)  # refresh once per second so labels reflect current inputs without pressing Enter
def you_indicated_block():
    # live reads from session_state
    item_2d_balance = (st.session_state.get(K("alleged_chargeoff_balance_4")) or "").strip()
    item_2e = (st.session_state.get(K("alleged_last_payment_date_5")) or "").strip()
    item_2d = (st.session_state.get(K("alleged_default_date_5b")) or "").strip()
    item_2i = (st.session_state.get(K("postchargeoffpurchaserinfo_0")) or "").strip()

    # 7) charge-off balance
    st.markdown(f"### 7) You indicated that the complaint alleged that the debt balance at charge-off was `{item_2d_balance}`.")
    st.radio(
        "Is there an exhibit in the declaration that proves the debt balance at charge-off alleged in the complaint (CA Civil Code § 1788.58 (a)(4))?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("haschargeoffbalance_1", "Unclear")) if dget("haschargeoffbalance_1") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("haschargeoffbalance_1")
    )
    st.text_input(
        "Please indicate the document ID and exhibit number where this proof exists.  For example, Bill of Sale, attached in Complaint, 22IWUD01199_87855599, at 17 (Exhibit A)). Otherwise, leave the field blank",
        dget("chargeoff_balance_ref"), key=K("chargeoff_balance_ref")
    )

    # 8) dates
    st.markdown(
        f"""
        <h3>8) You indicated that the complaint alleged that the date of last payment was 
        <code>{item_2e}</code> and/or that the date of default was <code>{item_2d}</code>.
        A complaint’s filing date is provided on the left sidebar.</h3>
        """,
        unsafe_allow_html=True
    )

    st.radio(
        "Is there an exhibit in the declaration that substantiates the date of the defendant's last payment (or the date of default if no last payment) alleged in the complaint (CA Civil Code § 1788.58 (a)(5))?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("lastpaymentdate_1", "Unclear")) if dget("lastpaymentdate_1") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("lastpaymentdate_1")
    )
    st.text_input(
        "What is the date of last payment substantiated in an exhibit? (MM/DD/YYYY or 'None')",
        dget("substantiated_last_payment_date"), key=K("substantiated_last_payment_date")
    )
    st.text_input(
        "Please indicate the document ID and exhibit number where this proof exists for the previous item.",
        dget("substantiated_last_payment_ref"), key=K("substantiated_last_payment_ref")
    )
    st.radio(
        "Does the complaint meet the statute of limitations (4 years) given the complaint filing date (see the left sidebar) and any substantiated date of default or date of a payment made by the defendant ?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("lastpaymentdate_2", "Unclear")) if dget("lastpaymentdate_2") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("lastpaymentdate_2")
    )


    # 9) charge-off creditor info
    alleged_co_info = st.session_state.get(K("alleged_chargeoff_creditor_info_6"), dget("alleged_chargeoff_creditor_info_6", ""))
    st.markdown(
        f"### 9) You indicated that the complaint alleged the name and address of the charge-off creditor as `{alleged_co_info}`."
    )
    st.radio(
        "Is there an exhibit in the declaration that proves the alleged name and address of the charge-off creditor?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("chargeoffcreditorinfo_1", "Unclear")) if dget("chargeoffcreditorinfo_1") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("chargeoffcreditorinfo_1")
    )
    st.text_input(
        "Please indicate the document ID and exhibit number where this proof exists.",
        dget("chargeoff_creditor_ref"), key=K("chargeoff_creditor_ref")
    )

    # 10) debtor info
    alleged_debtor_addr = st.session_state.get(
        K("alleged_debtor_last_known_address_7"),
        dget("alleged_debtor_last_known_address_7", "")
    )

    st.markdown(
        f"### 10) You indicated that the complaint alleged the defendant’s last known address as `{alleged_debtor_addr or '—'}`."
    )
    st.radio(
        "Is there an exhibit in the declaration from the charge-off creditor that proves the alleged name and address(es) of the defendant?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("debtorinfo_1", "Unclear")) if dget("debtorinfo_1") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("debtorinfo_1")
    )
    st.text_input(
        "Please indicate the document ID and exhibit number where this proof exists.",
        dget("debtor_info_ref"), key=K("debtor_info_ref")
    )

    # 11) post-charge-off owners
    st.markdown(f"### 11) You indicated that the complaint alleged the post-charge-off owners of the debt to be: `{item_2i}`.")
    st.radio(
        "Are there bills of sale or some transfer record in the exhibits of the declaration for each alleged owner of the debt (CA Civil Code § 1788.58 (a)(8))?",
        YES_NO_UNSURE,
        index=YES_NO_UNSURE.index(dget("postchargeoffpurchaserinfo_1", "Unclear")) if dget("postchargeoffpurchaserinfo_1") in YES_NO_UNSURE else 2,
        horizontal=True, key=K("postchargeoffpurchaserinfo_1")
    )
    st.text_input(
        "Please indicate the document ID and exhibit number where this proof exists. Separate entries with semicolons.",
        dget("post_charge_off_purchaser_refs"), key=K("post_charge_off_purchaser_refs")
    )

        # 12) Attorneys’ fees
    fees_amount = st.session_state.get(K("rfdj_attorney_fees_amount")) or ""
    if fees_amount and fees_amount.strip() not in ("", "0", "0.0"):
        st.markdown(
            f"### 12) Attorneys’ fees\n"
            f"You found that attorney fees were **{fees_amount}**.\n\n"
            "A plaintiff can only get attorneys’ fees if requested in the prayer of the complaint "
            "and if the original debt instrument provides for attorneys’ fees in the event of breach. "
            "Often, plaintiffs will attach a separate declaration to substantiate this."
        )

        st.radio(
            "Has the plaintiff either attached a separate Declaration in Support of Request for Attorneys’ Fees "
            "or included proof within the other declarations (e.g., the 1788 or 585 declaration) that the original debt instrument provided for attorneys’ fees?",
            YES_NO_UNSURE,
            index=YES_NO_UNSURE.index(dget("fees_proof_present", "Unclear"))
                if dget("fees_proof_present") in YES_NO_UNSURE else 2,
            horizontal=True, key=K("fees_proof_present")
        )

        st.text_input(
            "Please indicate the document ID and exhibit number where this proof exists. "
            "For example: Promissory Note attached in the Declaration, 22IWUD01199_87855599, at 17 (Exhibit A).",
            dget("fees_proof_ref"), key=K("fees_proof_ref")
        )


you_indicated_block()

# ─────────────────────────────────────────────────────────────────────────────
# FINAL RECOMMENDATION (no auto-suggestion; student decides)
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("### Final Recommendation")
st.caption("No automatic suggestion. Make your own determination based on the evidence above.")
st.radio(
    "Your recommendation",
    ["Grant", "Reject – Major", "Reject – Minor", "Undecided"],
    index=(["Grant", "Reject – Major", "Reject – Minor", "Undecided"].index(dget("final_recommendation"))
           if dget("final_recommendation") in ["Grant", "Reject – Major", "Reject – Minor", "Undecided"] else 3),
    horizontal=True,
    key=K("final_recommendation"),
)
st.text_area(
    "Notes (optional): summarize key items and rationale",
    dget("final_notes"), key=K("final_notes"), height=120
)

c1, c2 = st.columns(2)
save_draft = c1.button("💾 Save Draft", use_container_width=True, key=K("btn_save_draft"))
save_final = c2.button("✅ Save & Mark Complete", type="primary", use_container_width=True, key=K("btn_save_final"))

# ─────────────────────────────────────────────────────────────────────────────
# STASH ANSWERS FOR AUTOSAVE (EXACT COLUMN NAMES)
# ─────────────────────────────────────────────────────────────────────────────
answers = {
    # metadata (created_at is set in upsert; time_caselevel computed)
    "time_caselevel": 0.0,  # will be overwritten by collect_payload()

    # 1) rfdj amounts / complaint
    "rfdj_demand_amount": st.session_state.get(K("rfdj_demand_amount"), ""),
    "rfdj_interest_amount": st.session_state.get(K("rfdj_interest_amount"), ""),
    "rfdj_cost_amount": st.session_state.get(K("rfdj_cost_amount"), ""),
    "rfdj_attorney_fees_amount": st.session_state.get(K("rfdj_attorney_fees_amount"), ""),
    "complaint_has_prayer": st.session_state.get(K("complaint_has_prayer"), "Unclear"),
    "rfdjamount_1": st.session_state.get(K("rfdjamount_1"), "Unclear"),

    # 2) allegations
    "allegation_debt_buyer_1": st.session_state.get(K("allegation_debt_buyer_1"), "Unclear"),
    "allegation_nature_of_debt_2": st.session_state.get(K("allegation_nature_of_debt_2"), "Unclear"),
    "allegation_sole_owner_3": st.session_state.get(K("allegation_sole_owner_3"), "Unclear"),

    "alleges_chargeoff_balance_4": st.session_state.get(K("alleges_chargeoff_balance_4"), "Unclear"),
    "alleged_chargeoff_balance_4": st.session_state.get(K("alleged_chargeoff_balance_4"), ""),

    "alleges_last_payment_date_5": st.session_state.get(K("alleges_last_payment_date_5"), "Unclear"),
    "alleged_last_payment_date_5": st.session_state.get(K("alleged_last_payment_date_5"), ""),

    "alleges_default_date_5b": st.session_state.get(K("alleges_default_date_5b"), "Unclear"),
    "alleged_default_date_5b": st.session_state.get(K("alleged_default_date_5b"), ""),

    "alleges_chargeoff_creditor_info_6": st.session_state.get(K("alleges_chargeoff_creditor_info_6"), "Unclear"),
    "alleged_chargeoff_creditor_info_6": st.session_state.get(K("alleged_chargeoff_creditor_info_6"), ""),

    "alleges_debtor_last_known_address_7": st.session_state.get(K("alleges_debtor_last_known_address_7"), "Unclear"),
    "alleged_debtor_last_known_address_7": st.session_state.get(K("alleged_debtor_last_known_address_7"), ""),

    "alleges_postchargeoffpurchaserinfo_0": st.session_state.get(K("alleges_postchargeoffpurchaserinfo_0"), "Unclear"),
    "postchargeoffpurchaserinfo_0": st.session_state.get(K("postchargeoffpurchaserinfo_0"), ""),

    "allegation_1788_52_compliance": st.session_state.get(K("allegation_1788_52_compliance"), "Unclear"),

    # 3) agreement in complaint
    "hascontractorlaststatement_0": st.session_state.get(K("hascontractorlaststatement_0"), "Unclear"),
    "complaint_agreement_ref": st.session_state.get(K("complaint_agreement_ref"), ""),

    # 4) sworn declaration (dotted key handled during upsert)
    "hasSignedSworn1788.60_0": st.session_state.get(K("hasSignedSworn1788.60_0"), "Unclear"),
    "declaration_pkm_present": st.session_state.get(K("declaration_pkm_present"), "Unclear"),
    "chain_position_number": st.session_state.get(K("chain_position_number"), ""),
    "declaration_prior_buyers_pkm": st.session_state.get(K("declaration_prior_buyers_pkm"), "Unclear"),
    "declaration_pkm_refs": st.session_state.get(K("declaration_pkm_refs"), ""),

    # 5) agreement in declaration
    "declaration_has_agreement_proof": st.session_state.get(K("declaration_has_agreement_proof"), "Unclear"),
    "declaration_agreement_ref": st.session_state.get(K("declaration_agreement_ref"), ""),

    # 6) ownership chain
    "ownership_chain_sufficient": st.session_state.get(K("ownership_chain_sufficient"), "Unclear"),
    "ownership_chain_account_match": st.session_state.get(K("ownership_chain_account_match"), "Unclear"),
    "ownership_chain_refs": st.session_state.get(K("ownership_chain_refs"), ""),

    # 7–11) checks
    "haschargeoffbalance_1": st.session_state.get(K("haschargeoffbalance_1"), "Unclear"),
    "chargeoff_balance_ref": st.session_state.get(K("chargeoff_balance_ref"), ""),

    "lastpaymentdate_1": st.session_state.get(K("lastpaymentdate_1"), "Unclear"),
    "substantiated_last_payment_date": st.session_state.get(K("substantiated_last_payment_date"), ""),
    "substantiated_last_payment_ref": st.session_state.get(K("substantiated_last_payment_ref"), ""),
    "lastpaymentdate_2": st.session_state.get(K("lastpaymentdate_2"), "Unclear"),

    "chargeoffcreditorinfo_1": st.session_state.get(K("chargeoffcreditorinfo_1"), "Unclear"),
    "chargeoff_creditor_ref": st.session_state.get(K("chargeoff_creditor_ref"), ""),

    "debtorinfo_1": st.session_state.get(K("debtorinfo_1"), "Unclear"),
    "debtor_info_ref": st.session_state.get(K("debtor_info_ref"), ""),

    "postchargeoffpurchaserinfo_1": st.session_state.get(K("postchargeoffpurchaserinfo_1"), "Unclear"),
    "post_charge_off_purchaser_refs": st.session_state.get(K("post_charge_off_purchaser_refs"), ""),

    # fees
    "fees_proof_present": st.session_state.get(K("fees_proof_present"), "Unclear"),
    "fees_proof_ref": st.session_state.get(K("fees_proof_ref"), ""),

    # final
    "final_recommendation": st.session_state.get(K("final_recommendation"), "Undecided"),
    "final_notes": st.session_state.get(K("final_notes"), ""),

    # identity (added at upsert boundary too)
    "case_number": case_number,
    "annotator_id": annotator_id,
}
st.session_state[f"answers:{case_number}"] = answers  # single source of truth for autosave

# ─────────────────────────────────────────────────────────────────────────────
# PAYLOAD & AUTOSAVE
# ─────────────────────────────────────────────────────────────────────────────
def collect_payload() -> Dict[str, Any]:
    started_base = st.session_state["case_elapsed_seconds"][case_number]
    opened_at = st.session_state["case_open_time"][case_number]
    now = datetime.now(timezone.utc)
    elapsed_total = started_base + (now - opened_at).total_seconds()
    payload = st.session_state.get(f"answers:{case_number}", {}).copy()
    payload["time_caselevel"] = float(elapsed_total)
    return payload



@st.fragment(run_every=10)
def autosave_fragment():
    """
    Every 10s:
    - upsert results to training_results_gold
    - mark case as draft (scoped by annotator to avoid collisions)
    - show toast on success/failure
    """
    try:
        payload = collect_payload()
        upsert_results_gold(case_number, annotator_id, payload)
        set_case_progress(case_number, "draft", annotator_id)
        st.session_state["last_autosave_time"] = datetime.now().strftime("%H:%M:%S")  # local display
        st.toast(f"💾 Autosaved at {st.session_state['last_autosave_time']}", icon="💾")
    except Exception as e:
        st.session_state["last_autosave_error"] = str(e)
        st.toast(f"⚠️ Autosave failed: {e}", icon="⚠️")
autosave_fragment()

if "last_autosave_time" in st.session_state:
    st.sidebar.caption(f"💾 Autosaved at {st.session_state['last_autosave_time']}")
elif "last_autosave_error" in st.session_state:
    st.sidebar.caption(f"⚠️ Autosave failed: {st.session_state['last_autosave_error']}")
else:
    st.sidebar.caption("⏳ Autosave running...")

# ─────────────────────────────────────────────────────────────────────────────
# SAVE (Draft/Complete)
# ─────────────────────────────────────────────────────────────────────────────
if (save_draft or save_final):
    try:
        payload = collect_payload()
        upsert_results_gold(
            case_number,
            annotator_id,
            payload,
            is_final=save_final
        )

        set_case_progress(case_number, "complete" if save_final else "draft", annotator_id)

        if save_final:
            insert_fallback_snapshot(case_number, annotator_id, payload)
            # Pick next case BEFORE rerun
            cases = fetch_cases_for_annotator(annotator_id)
            nxt = get_next_incomplete_or_draft_after(case_number, cases)
            if nxt:
                st.session_state["direct_case_mode"] = True
                st.session_state["direct_case_number"] = nxt["case_number"]
                # one clean rerun; new case renders at the top naturally
                st.session_state["scroll_to_top"] = True
                st.rerun()
            else:
                # No more cases - redirect to completed page
                st.success("🎉 All cases completed!")
                import time
                time.sleep(1)
                st.session_state.pop("scroll_to_top", None)
                st.session_state.pop("direct_case_mode", None)
                st.session_state.pop("direct_case_number", None)
                st.switch_page("pages/2_cases_completed.py")
        else:
            st.success("Saved ✅ (draft)")
    except Exception as e:
        st.error(f"Failed to save: {e}")

