import os
from dotenv import load_dotenv
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.prompts import ChatPromptTemplate
from langgraph.types import interrupt, Command
from langchain_groq import ChatGroq
from langchain_community.utilities import SQLDatabase
from schema import DatabaseWriteAction, UserPayload
from langsmith import traceable
load_dotenv()
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "TEST_GUARDRAIL"

# 1.SHARED GRAPH STATE DEFINITION
class AgentState(TypedDict):
    question: str
    generated_sql: Optional[str]
    write_action: Optional[DatabaseWriteAction]
    validation_error: Optional[str]
    is_approved: Optional[bool]
    query_result: Optional[str]
    final_response: Optional[str]


#2.DATABASE & LLM INITIALIZATION
llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0)

db = SQLDatabase.from_uri("sqlite:///company.db")


db.run("DROP TABLE IF EXISTS users;")
db.run("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    name TEXT NOT NULL, 
    role TEXT NOT NULL,
    department TEXT NOT NULL,
    salary REAL NOT NULL,
    join_date TEXT NOT NULL
);
""")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Alice', 'Admin', 'Engineering', 95000.00, '2023-01-15');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Bob', 'User', 'Engineering', 72000.00, '2024-03-10');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Carlos', 'Manager', 'Sales', 85000.00, '2022-07-19');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Diana', 'User', 'Sales', 55000.00, '2025-11-01');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Ethan', 'Manager', 'HR', 65000.00, '2021-02-25');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('Fiona', 'User', 'Finance', 110000.00, '2023-09-05');")
db.run("INSERT INTO users (name, role, department, salary, join_date) VALUES ('George', 'User', 'Engineering', 88000.00, '2024-06-20');")

schema_info = db.get_table_info()

# 3.SUBGRAPH: SECURITY GATE & HITL INTERRUPT
@traceable(name="Safety Gate", run_type="chain")
def check_safety_and_gate(state: AgentState):
    sql = (state.get("generated_sql") or "").upper()
    destructive_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER"]
    
    if any(keyword in sql for keyword in destructive_keywords):
        # The graph pauses execution RIGHT HERE. It raises an interrupt containing context 
        # that the frontend UI can capture to show the administrator.
        human_decision = interrupt(
            {
                "message": "Security Warning: A destructive query requires review.",
                "sql": state["generated_sql"]
            }
        )
        # When resumed via Command(resume=True/False), the value passes directly into this variable
        return {"is_approved": human_decision}
        
    return {"is_approved": True}

# Build the modern, streamlined Subgraph
subgraph_workflow = StateGraph(AgentState)
subgraph_workflow.add_node("safety_layer", check_safety_and_gate)
subgraph_workflow.set_entry_point("safety_layer")
subgraph_workflow.add_edge("safety_layer", END)

subgraph_checkpointer = MemorySaver()
approval_subgraph = subgraph_workflow.compile(checkpointer=subgraph_checkpointer)

# 4.PARENT GRAPH: CORE ANALYTIC AGENT
@traceable(name="Parse and Validate Input", run_type="chain")
def parse_and_validate_input(state: AgentState):
    structured_llm = llm.with_structured_output(
        DatabaseWriteAction,
        method="json_schema",
        strict=False,
    )

    extraction_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
You are a request parser for a SQLite employee database.

Available table:
users(
    id INTEGER,
    name TEXT,
    role TEXT,
    department TEXT,
    salary REAL,
    join_date TEXT
)

Return exactly one DatabaseWriteAction object.
Never reply with explanations, questions, markdown, or normal text.

Rules:
- The only target table is "users".
- Requests to list, count, average, find minimum/maximum, group, sort,
  filter, or analyze employees are SELECT actions.
- Requests to add an employee are INSERT actions.
- Requests to change employee information are UPDATE actions.
- Requests to change table columns are ALTER actions.
- For SELECT, payload and alter_statement should be null.
- For INSERT or UPDATE, alter_statement should be null.
                """,
            ),
            ("human", "{question}"),
        ]
    )

    chain = extraction_prompt | structured_llm

    try:
        action_data = chain.invoke(
            {
                "question": state["question"],
            }
        )

        if (
            action_data.action_type in ["INSERT", "UPDATE"]
            and action_data.target_table == "users"
        ):
            if not action_data.payload:
                raise ValueError(
                    f"{action_data.action_type} requires employee data."
                )

            validated_payload = UserPayload(**action_data.payload)
            action_data.payload = validated_payload.model_dump()

        return {
            "write_action": action_data,
            "validation_error": None,
        }

    except Exception as e:
        return {
            "validation_error": f"Data Schema Validation Failed: {str(e)}",
            "final_response": f"Data Schema Validation Failed: {str(e)}",
        }
    
@traceable(name="Generate SQL", run_type="chain")
def generate_sql(state: AgentState): #generating the sql query based on the user input and the schema
    if state.get("validation_error"):
        return {}
    
    action = state["write_action"]
    
    
    if action.action_type == "SELECT":
        gen_prompt = ChatPromptTemplate.from_template(
            "You are an expert analytics SQL engine. Given the following database schema, write a valid "
            "SQL query to fulfill the request. You can use aggregations (AVG, MIN, MAX), GROUP BY, and ORDER BY.\n"
            "Output ONLY the raw SQL query script. No markdown code blocks, backticks, or wrapping formatting.\n\n"
            "Schema:\n{schema}\n\n"
            "Question: {question}"
        )
        sql = (gen_prompt | llm).invoke({"schema": schema_info, "question": state["question"]}).content.strip()
        
    
    elif action.action_type == "INSERT":
        columns = ", ".join(action.payload.keys())
        values = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in action.payload.values()])
        sql = f"INSERT INTO {action.target_table} ({columns}) VALUES ({values});"
        
    elif action.action_type == "UPDATE":
        sets = ", ".join([f"{k} = '{v}'" if isinstance(v, str) else f"{k} = {v}" for k, v in action.payload.items()])
        sql = f"UPDATE {action.target_table} SET {sets} WHERE id = 1;"
        
    elif action.action_type == "ALTER":
        sql = action.alter_statement
        
    return {"generated_sql": sql}

@traceable(name="Route After SQL Generation", run_type="chain")
def route_after_generation(state: AgentState): #if validation error occur pass to fornmulate answer else pass to subgraph
    if state.get("validation_error"):
        return "formulate_answer"
    return "approval_subgraph"

@traceable(name="Execute SQLite Query", run_type="tool")
def execute_query(state: AgentState): #if get approved from user then execute the query 
    if state.get("is_approved") is False:
        return {"final_response": "Security Rejection: Operation blocked by Administrator review policy."}
        
    try:
        result = db.run(state["generated_sql"])
        
        return {"query_result": str(result) if result else "Success (Database records modified successfully)."}
    except Exception as e:
        return {"query_result": f"Runtime Execution Error: {str(e)}"}
    
@traceable(name="Formulate Final Answer", run_type="chain")
def formulate_answer(state: AgentState): #gives the final response to the user based on the query result and the generated sql
    if state.get("final_response"):
        return {}
        
    answer_prompt = ChatPromptTemplate.from_template(
        "You are an insightful data assistant. Summarize the database outcome context clearly for the user.\n"
        "Question: {question}\n"
        "SQL Executed: {generated_sql}\n"
        "Database Result Output: {query_result}"
    )
    response = (answer_prompt | llm).invoke({
        "question": state["question"], 
        "generated_sql": state["generated_sql"], 
        "query_result": state["query_result"]
    }).content
    return {"final_response": response}


parent_workflow = StateGraph(AgentState)
parent_workflow.add_node("parse_and_validate", parse_and_validate_input)
parent_workflow.add_node("generate_sql", generate_sql)
parent_workflow.add_node("approval_subgraph", approval_subgraph)
parent_workflow.add_node("execute_query", execute_query)
parent_workflow.add_node("formulate_answer", formulate_answer)

parent_workflow.set_entry_point("parse_and_validate")
parent_workflow.add_edge("parse_and_validate", "generate_sql")
parent_workflow.add_conditional_edges("generate_sql", route_after_generation)
parent_workflow.add_edge("approval_subgraph", "execute_query")
parent_workflow.add_edge("execute_query", "formulate_answer")
parent_workflow.add_edge("formulate_answer", END)

parent_checkpointer = MemorySaver()
agent_app = parent_workflow.compile(checkpointer=parent_checkpointer)