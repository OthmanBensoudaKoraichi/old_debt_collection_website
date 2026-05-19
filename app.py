import streamlit as st
from supabase import create_client
from typing import Dict, Any, List, Set

st.set_page_config(page_title="Debt Collection • Gold Labels", layout="wide")

# ─────────────────────────────────────────────────────────────────────────────
# One-time Supabase client initialization
# ─────────────────────────────────────────────────────────────────────────────
if "supabase" not in st.session_state:
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        st.error("Missing Supabase credentials in st.secrets.")
        st.stop()
    st.session_state["supabase"] = create_client(url, key)

supabase = st.session_state["supabase"]

st.title("Debt Collection – Gold Label Annotation")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_cases_for_annotator(annotator_id: str) -> List[Dict[str, Any]]:
    """Fetch all recoding cases assigned to this annotator from cases_gold."""
    res = (
        supabase.table("cases_gold")
        .select("*")
        .ilike("annotator_id", annotator_id)
        .eq("recoding", 1)
        .execute()
    )
    return res.data or []

def _rows_to_assigned_cases(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize cases into [{'case_id': '...', 'branch': None}] format."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        case_number = r.get("case_number") or r.get("CASE_NUMBER")
        if case_number:
            out.append({"case_id": str(case_number), "branch": None})
    return out

def _rows_to_progress_keys(rows: List[Dict[str, Any]]) -> Set[str]:
    """
    Build a set of 'case_id::branch' keys for cases marked complete.
    Uses progress='complete' instead of completed=True.
    """
    keys: Set[str] = set()
    for r in rows:
        prog = (r.get("progress") or r.get("PROGRESS") or "").lower()
        if prog == "complete":
            cid = r.get("case_number") or r.get("CASE_NUMBER")
            if cid:
                keys.add(f"{cid}::None")
    return keys

# ─────────────────────────────────────────────────────────────────────────────
# Login form (with optional case jump)
# ─────────────────────────────────────────────────────────────────────────────
with st.form("login"):
    annotator_id_input = st.text_input("Annotator ID (e.g., Brian, Parker, Victor)", key="widget_annotator_id")
    case_number_input = st.text_input("Optional: Jump directly to a specific case number (e.g., 24CHLC33360)", key="widget_case_number")
    submitted = st.form_submit_button("Start annotating", type="primary")

if submitted:
    annotator_raw = (annotator_id_input or "").strip()
    if not annotator_raw:
        st.error("Please enter an Annotator ID.")
        st.stop()

    # Pull all cases for this annotator
    rows = _fetch_cases_for_annotator(annotator_raw)

    if not rows:
        st.warning(f"No cases found for annotator '{annotator_raw}'.")
        st.stop()

    # Normalize annotator capitalization
    canonical_annotator = None
    for r in rows:
        val = (r.get("annotator_id") or "").strip()
        if val:
            canonical_annotator = val
            break
    annotator_id = canonical_annotator or annotator_raw

    # Prepare progress info
    assigned_norm = _rows_to_assigned_cases(rows)
    completed_keys = _rows_to_progress_keys(rows)

    total_cases = len(assigned_norm)
    completed_count = sum(1 for it in assigned_norm if f"{it['case_id']}::{it['branch']}" in completed_keys)
    current_case_number = completed_count + 1

    # Save session state
    st.session_state["annotator_id"] = annotator_id
    st.session_state["assigned_cases"] = assigned_norm
    st.session_state["completed_keyset"] = completed_keys
    st.session_state["total_cases"] = total_cases
    st.session_state["current_case_number"] = current_case_number

    if total_cases == 0:
        st.warning(f"No cases have been assigned to '{annotator_id}'.")
        st.stop()

    # ─────────────────────────────────────────────────────────────
    # NEW: Direct navigation to a case if provided
    # ─────────────────────────────────────────────────────────────
    case_number_raw = (case_number_input or "").strip()
    if case_number_raw:
        # Verify that this case belongs to this annotator
        case_match = None
        for r in rows:
            cn = str(r.get("case_number") or r.get("CASE_NUMBER"))
            if cn == case_number_raw:
                case_match = cn
                break

        if not case_match:
            st.error(f"Case '{case_number_raw}' not found or not assigned to '{annotator_id}'.")
            st.stop()

        # Save the chosen case in session for direct load
        st.session_state["direct_case_number"] = case_match
        st.session_state["direct_case_mode"] = True
        st.switch_page("pages/1_annotate.py")

    else:
        # Default behavior: go to the next incomplete case
        st.session_state["direct_case_mode"] = False
        st.switch_page("pages/1_annotate.py")
