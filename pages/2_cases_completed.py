import streamlit as st

st.set_page_config(page_title="All done", layout="centered")
st.title("🎉 All assigned cases completed")
st.write("Thank you for annotating.")
if st.button("Back to start"):
    st.switch_page("app.py")


