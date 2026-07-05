import uuid

import streamlit as st
from backend import agent_app, db
from langgraph.types import Command


st.set_page_config(page_title="Secure Text-to-SQL Portal", layout="wide")
st.title("Secure Text-to-SQL Agent with HITL & Pydantic")


# 1. SIDEBAR: LIVE DATABASE MONITOR
with st.sidebar:
    st.header("Current Database State (`users`)")

    try:
        data = db.run("SELECT * FROM users;")
        st.code(data, language="text")
    except Exception as e:
        st.error(str(e))

    st.markdown("---")

    st.info(
        "💡 **Schema Reference:**\n"
        "- **Roles:** `Admin`, `User`, `Manager`\n"
        "- **Departments:** `Engineering`, `HR`, `Sales`, `Marketing`, `Finance`"
    )


# 2. SESSION STATE
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None

if "awaiting_approval" not in st.session_state:
    st.session_state.awaiting_approval = False

if "pending_sql" not in st.session_state:
    st.session_state.pending_sql = ""

if "last_response" not in st.session_state:
    st.session_state.last_response = ""

if "last_status" not in st.session_state:
    st.session_state.last_status = ""


def get_config():
    return {
        "configurable": {
            "thread_id": st.session_state.thread_id
        }
    }


# 3. USER INPUT
user_prompt = st.text_input(
    "Enter natural language query command:",
    placeholder="e.g., What is the average salary in the Engineering department?"
)


if st.button("Execute Request") and user_prompt:
    # Critical fix:
    # Every brand-new request gets a fresh graph state.
    # Old approval results cannot affect the next request.
    st.session_state.thread_id = f"request-{uuid.uuid4()}"

    st.session_state.awaiting_approval = False
    st.session_state.pending_sql = ""
    st.session_state.last_response = ""
    st.session_state.last_status = ""

    config = get_config()

    with st.spinner("Evaluating Graph Operations..."):
        for _ in agent_app.stream(
            {"question": user_prompt},
            config=config
        ):
            pass

    snapshot = agent_app.get_state(config)

    interrupts = []

    for task in snapshot.tasks:
        interrupts.extend(task.interrupts or [])

    # Query paused for admin approval
    if interrupts:
        interrupt_details = interrupts[0].value

        st.session_state.awaiting_approval = True
        st.session_state.pending_sql = interrupt_details.get("sql", "")

    # Query finished without requiring approval
    else:
        final_state = snapshot.values
        error = final_state.get("validation_error")
        response = final_state.get("final_response")

        if error:
            st.session_state.last_status = "error"
            st.session_state.last_response = error

        elif response:
            st.session_state.last_status = "success"
            st.session_state.last_response = response

        else:
            st.session_state.last_status = "warning"
            st.session_state.last_response = (
                "The request finished without a final response."
            )


# 4. HUMAN-IN-THE-LOOP APPROVAL
if st.session_state.awaiting_approval:
    st.warning(
        "Action Required: A destructive or structural query was intercepted."
    )

    st.markdown("### Generated Query for Review:")
    st.code(st.session_state.pending_sql, language="sql")

    config = get_config()

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Approve & Execute Query", use_container_width=True):
            with st.spinner("Executing approved query..."):
                for _ in agent_app.stream(
                    Command(resume=True),
                    config=config
                ):
                    pass

            final_state = agent_app.get_state(config).values

            st.session_state.awaiting_approval = False
            st.session_state.pending_sql = ""

            if final_state.get("validation_error"):
                st.session_state.last_status = "error"
                st.session_state.last_response = final_state["validation_error"]

            elif final_state.get("final_response"):
                st.session_state.last_status = "success"
                st.session_state.last_response = final_state["final_response"]

            else:
                st.session_state.last_status = "warning"
                st.session_state.last_response = (
                    "The approved query completed without a final response."
                )

            st.rerun()

    with col2:
        if st.button("Reject & Terminate Operation", use_container_width=True):
            with st.spinner("Rejecting query..."):
                for _ in agent_app.stream(
                    Command(resume=False),
                    config=config
                ):
                    pass

            final_state = agent_app.get_state(config).values

            st.session_state.awaiting_approval = False
            st.session_state.pending_sql = ""
            st.session_state.last_status = "error"

            st.session_state.last_response = final_state.get(
                "final_response",
                "Security Rejection: Operation blocked by Administrator review policy."
            )

            st.rerun()


# 5. FINAL RESULT DISPLAY
if st.session_state.last_response:
    if st.session_state.last_status == "success":
        st.success("Execution Complete!")
        st.write(st.session_state.last_response)

    elif st.session_state.last_status == "error":
        st.error(st.session_state.last_response)

    else:
        st.warning(st.session_state.last_response)