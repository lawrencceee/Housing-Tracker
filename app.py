from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from notion_client import Client
from datetime import datetime, timedelta
import streamlit as st
import os
import json
import re

load_dotenv()

DATABASE_ID = "244aef75cd248040aee9fbbe4a05e42f"

try:
    notion = Client(auth=st.secrets["NOTION_API_KEY"])
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
except Exception as e:
    st.error(f"Failed to initialize clients. Please check your API keys in Streamlit secrets. Error: {e}")
    st.stop()

# ✅ Set environment variables for LangChain using Streamlit secrets
openai.api_key = st.secrets["OPENAI_API_KEY"]
os.environ["LANGCHAIN_PROJECT"] = "NotionHouseTrackerProject"
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = st.secrets["LANGCHAIN_API_KEY"]


today = datetime.now().date()
yesterday = today - timedelta(days=1)
last_week = today - timedelta(days=7)

HOUSING_TYPES = ["Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom+", "House"]
STATUS_OPTIONS = ["Not yet applied", "Applied", "Rejected", "Accepted", "Interview/Tour", "Waitlisted"]

def create_notion_page(**kwargs):
    """Creates a new page in the Notion database with dynamically built properties."""
    
    properties = {
        "Property Name": {"title": [{"text": {"content": kwargs.get("property_name", "Unknown Property")}}]},
        "Application Date": {"date": {"start": kwargs.get("application_date") or today.isoformat()}},
        "Status": {"status": {"name": kwargs.get("status", "Applied")}}
    }

    website_link = kwargs.get("website_link")
    if website_link:
        properties["Website Link"] = {"url": website_link}
        
    housing_type = kwargs.get("housing_type")
    if housing_type and housing_type in HOUSING_TYPES:
        properties["Housing Type Needed"] = {"select": {"name": housing_type}}
        
    contact_info = kwargs.get("contact_info")
    if contact_info:
        properties["Contact Information"] = {"rich_text": [{"text": {"content": contact_info}}]}
        
    location = kwargs.get("location")
    if location:
        properties["Location"] = {"rich_text": [{"text": {"content": location}}]}

    notion.pages.create(parent={"database_id": DATABASE_ID}, properties=properties)


def update_notion_status(property_name: str, new_status: str) -> str:
    """Finds a page by property name and updates its status."""
    
    search_results = notion.databases.query(
        database_id=DATABASE_ID,
        filter={"property": "Property Name", "title": {"contains": property_name}}
    )

    if not search_results["results"]:
        raise ValueError(f"No entry found with property name containing: {property_name}")

    page = search_results["results"][0]
    page_id = page["id"]
    
    title_data = page["properties"].get("Property Name", {}).get("title", [])
    full_property_name = title_data[0]["text"]["content"] if title_data else property_name

    notion.pages.update(
        page_id=page_id,
        properties={
            "Status": {"status": {"name": new_status} if new_status in STATUS_OPTIONS else {"name": "Applied"}}
        }
    )
    return full_property_name


def get_filter_from_llm(nl_prompt: str) -> dict:
    """Converts a natural language prompt into a Notion filter and sort JSON object using an LLM."""
    
    prompt = f"""
    You are an expert system that converts natural language into a Notion API JSON payload.
    The user is querying a house application tracker database.
    Today is {today.isoformat()}.

    DATABASE SCHEMA:
    - "Property Name": (Title)
    - "Application Date": (Date)
    - "Housing Type Needed": (Select) Options: {', '.join(HOUSING_TYPES)}
    - "Status": (Status) Options: {', '.join(STATUS_OPTIONS)}
    - "Location": (Rich Text)

    Your task is to generate a JSON object with two keys: "filter" and "sorts".
    - The "filter" object should match the user's query.
    - The "sorts" object should ALWAYS sort results by "Application Date" in descending order unless specified otherwise.

    EXAMPLES:
    User: "What houses did I apply to last week?"
    Output:
    {{
      "filter": {{
        "and": [
          {{"property": "Application Date", "date": {{"on_or_after": "{last_week.isoformat()}"}}}},
          {{"property": "Status", "status": {{"does_not_equal": "Not yet applied"}}}}
        ]
      }},
      "sorts": [{{"property": "Application Date", "direction": "descending"}}]
    }}

    User: "Show me rejected applications for 2 bedroom apartments"
    Output:
    {{
      "filter": {{
        "and": [
          {{"property": "Status", "status": {{"equals": "Rejected"}}}},
          {{"property": "Housing Type Needed", "select": {{"equals": "2 Bedroom"}}}}
        ]
      }},
      "sorts": [{{"property": "Application Date", "direction": "descending"}}]
    }}
    
    User: "What properties in Toronto did I apply to?"
    Output:
    {{
      "filter": {{
        "and": [
            {{"property": "Location", "rich_text": {{"contains": "Toronto"}}}},
            {{"property": "Status", "status": {{"does_not_equal": "Not yet applied"}}}}
        ]
      }},
      "sorts": [{{"property": "Application Date", "direction": "descending"}}]
    }}

    User: "list all my applications"
    Output:
    {{
        "filter": {{
            "property": "Application Date",
            "date": {{"is_not_empty": true}}
        }},
        "sorts": [{{"property": "Application Date", "direction": "descending"}}]
    }}

    Now, generate the JSON for the following user request. Only output the JSON object, nothing else.
    User: "{nl_prompt}"
    """
    response = llm.invoke(prompt).content
    json_str = response.strip()
    if json_str.startswith("```"):
        json_str = re.sub(r"```json|```", "", json_str).strip()
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON. Raw output:\n{response}") from e


def query_notion_database(payload: dict) -> list:
    """Queries the Notion database using the provided filter and sort payload."""
    results = notion.databases.query(database_id=DATABASE_ID, **payload)
    
    records = []
    for item in results["results"]:
        props = item["properties"]
        record = {}
        for name, prop_data in props.items():
            prop_type = prop_data["type"]
            value = None
            if prop_type == "title":
                value = prop_data["title"][0]["text"]["content"] if prop_data["title"] else ""
            elif prop_type == "rich_text":
                value = prop_data["rich_text"][0]["text"]["content"] if prop_data["rich_text"] else ""
            elif prop_type == "select":
                value = prop_data["select"]["name"] if prop_data["select"] else ""
            elif prop_type == "status":
                value = prop_data["status"]["name"] if prop_data["status"] else ""
            elif prop_type == "date":
                value = prop_data["date"]["start"] if prop_data["date"] else ""
            elif prop_type == "url":
                value = prop_data["url"]
            
            if value is not None:
                record[name] = value
                
    return records


def get_intent_and_payload(nl_prompt: str) -> dict:
    """Uses an LLM to determine the user's intent (create, update, query) and extract entities."""
    prompt = f"""
    You are an expert system that classifies a user's intent and extracts information for a house application tracker.
    Today is {today.isoformat()}. Yesterday was {yesterday.isoformat()}.

    Return JSON with:
    - "intent": "create", "update", or "query"
    - And relevant fields: "property_name", "website_link", "application_date", "housing_type", "contact_info", "status", "location".

    - For "create" or "update", "status" must be one of: {', '.join(STATUS_OPTIONS)}.
    - For "create", if status is not mentioned, default to "Applied". If the user says they "haven't applied yet", use "Not yet applied".

    EXAMPLES:
    Input: "I applied to Sunset Apartments yesterday for a 1 bedroom"
    Output: {{"intent": "create", "property_name": "Sunset Apartments", "housing_type": "1 Bedroom", "status": "Applied", "application_date": "{yesterday.isoformat()}"}}

    Input: "Oak Street House rejected my application"
    Output: {{"intent": "update", "property_name": "Oak Street House", "status": "Rejected"}}

    Input: "I applied to Blue Ridge Condos in Toronto. Website is [https://blueridge.com](https://blueridge.com). Contact is John Smith 555-1234"
    Output: {{"intent": "create", "property_name": "Blue Ridge Condos", "website_link": "[https://blueridge.com](https://blueridge.com)", "contact_info": "John Smith 555-1234", "location": "Toronto", "status": "Applied"}}

    Input: "Haven't applied to Maple Gardens yet but want to track it"
    Output: {{"intent": "create", "property_name": "Maple Gardens", "status": "Not yet applied"}}
    
    Input: "show me all my accepted applications"
    Output: {{"intent": "query"}}

    Input: "how many places have I applied to?"
    Output: {{"intent": "query"}}
    
    Now, classify the intent and extract the fields for the following input. Only output the JSON object.
    Input: "{nl_prompt}"
    """
    response = llm.invoke(prompt).content.strip()
    if response.startswith("```"):
        response = re.sub(r"```json|```", "", response).strip()
    return json.loads(response)


def analyze_records(records: list) -> str:
    """Generates a brief summary of the queried records."""
    if not records:
        return "No matching house applications found."
    
    total = len(records)
    status_counts = {}
    for r in records:
        status = r.get("Status", "N/A")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    analysis = f"Found **{total}** application(s). "
    breakdown = ", ".join([f"{count} {status}" for status, count in status_counts.items()])
    analysis += f"Breakdown: {breakdown}."
    
    return analysis


# --- 🖼️ STREAMLIT UI 🖼️ ---

st.set_page_config(page_title="Notion House Tracker AI", layout="centered")
st.title("🏠 House Application Tracker")
st.markdown("Track your house application here Kanojo~")

with st.expander("💡 Example Commands"):
    st.markdown("""
    **Adding:** `"I applied to Sunset Apartments yesterday for a 1 bedroom"`  
    **Updating:** `"Maple Gardens rejected my application"`  
    **Querying:** `"Show me all accepted applications"` or `"What did I apply to last week?"`
    """)

with st.form("notion_form"):
    nl_prompt = st.text_input("💬 What would you like to do?", placeholder="e.g., I applied to The Grand Residences...")
    submitted = st.form_submit_button("Enter ", use_container_width=True)

if submitted and nl_prompt:
    if DATABASE_ID == "your-house-tracker-database-id":
        st.error("❌ CONFIGURATION ERROR: Please replace 'your-house-tracker-database-id' in the script with your actual Notion Database ID.")
    else:
        try:
            with st.spinner("Kareshi is thinking..."):
                action = get_intent_and_payload(nl_prompt)
                intent = action.get("intent")

            if intent == "query":
                with st.spinner("🔍 Searching Notion..."):
                    notion_payload = get_filter_from_llm(nl_prompt)
                    records = query_notion_database(notion_payload)
                
                st.success(analyze_records(records))
                if records:
                    st.dataframe(records, use_container_width=True)

            elif intent == "create":
                with st.spinner("✍️ Creating entry in Notion..."):
                    create_notion_page(**action)
                st.success(f"✅ Application for **{action.get('property_name')}** has been created in Notion!")

            elif intent == "update":
                with st.spinner("🔄 Updating status in Notion..."):
                    full_name = update_notion_status(action["property_name"], action["status"])
                st.success(f"✅ Status for **{full_name}** updated to **{action['status']}**!")

            else:
                st.warning("⚠️ Could not determine your intent. Please try rephrasing.")

        except Exception as e:
            st.error(f"❌ An error occurred: {e}")
            st.info("Please check that your Notion Database ID is correct and that the integration has been shared with the database.")

st.markdown("---")

st.markdown("<div style='text-align: center;'>I love you bb</div>", unsafe_allow_html=True)

