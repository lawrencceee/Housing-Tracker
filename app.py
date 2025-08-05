from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from notion_client import Client
from datetime import datetime, timedelta
import streamlit as st
import dateparser
import os
import json
import re
import time

# Selenium Imports for Web Scraping
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

load_dotenv()

DATABASE_ID = "244aef75cd248040aee9fbbe4a05e42f"

try:
    notion = Client(auth=st.secrets["NOTION_API_KEY"])
    llm = ChatOpenAI(
    model="gpt-4o-mini",  # or "gpt-4o-mini", etc.
    temperature=0,
    openai_api_key=st.secrets["OPENAI_API_KEY"]
)
except Exception as e:
    st.error(f"Failed to initialize clients. Please check your API keys in Streamlit secrets. Error: {e}")
    st.stop()

# ✅ Set environment variables for LangChain using Streamlit secrets
os.environ["LANGCHAIN_PROJECT"] = "NotionHouseTrackerProject"
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = st.secrets["LANGCHAIN_API_KEY"]

today = datetime.now().date()
yesterday = today - timedelta(days=1)
last_week = today - timedelta(days=7)

# Database property options
HOUSING_TYPES = ["Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom+", "House"]
STATUS_OPTIONS = ["Not yet applied", "Applied", "Rejected", "Accepted", "Interview/Tour", "Waitlisted"]


# --- 🤖 CORE FUNCTIONS 🤖 ---

def extract_dublin_zone(address_text: str) -> str:
    """Extracts Dublin zone (D1, D2, etc.) from address text."""
    # Look for patterns like "Dublin 1", "Dublin 12", etc.
    dublin_pattern = r"Dublin\s+(\d{1,2})"
    match = re.search(dublin_pattern, address_text, re.IGNORECASE)
    if match:
        zone_number = match.group(1)
        return f"D{zone_number}"
    return None

def scrape_daft_ie(url: str) -> dict:
    """Scrapes a daft.ie URL using a headless browser on Streamlit Cloud."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    # Use the pre-installed chromium driver on Streamlit Cloud
    service = Service(executable_path="/usr/bin/chromedriver")
    
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    scraped_data = {}
    
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 15) # Increased wait time for cloud environment
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, '[data-testid="price"]')))
        
        # --- Extract data using Selenium's finders ---
        
        # Extract Price
        try:
            price_element = driver.find_element(By.CSS_SELECTOR, '[data-testid="price"]')
            price_text = price_element.text.replace(" per month", "").strip()
            scraped_data['price'] = price_text
        except Exception: 
            scraped_data['price'] = "Price not found"

        # Extract Address and Property Name
        try:
            address_element = driver.find_element(By.CSS_SELECTOR, '[data-testid="address"]')
            full_address = address_element.text
            
            # Split address parts
            address_parts = [part.strip() for part in full_address.split(',')]
            
            # Property name is the first part, cleaned up
            raw_property_name = address_parts[0] if address_parts else "Unknown Property"
            
            # Clean up common prefixes and bedroom info from property name
            property_name = raw_property_name.replace("Apartment ", "").replace("House ", "").replace("Studio ", "").strip()
            
            # Remove bedroom info patterns like "1 Bedroom", "2 Bed", etc.
            bedroom_patterns = [
                r'^\d+\s+Bedroom\s*',
                r'^\d+\s+Bed\s*',
                r'^\d+\s+BR\s*',
                r'^Studio\s*'
            ]
            for pattern in bedroom_patterns:
                property_name = re.sub(pattern, '', property_name, flags=re.IGNORECASE).strip()
            
            # Remove any leading symbols, numbers, or special characters
            property_name = re.sub(r'^[^\w\s]+', '', property_name).strip()  # Remove leading symbols
            property_name = re.sub(r'^\d+\s*', '', property_name).strip()    # Remove leading numbers
            
            # Clean up any remaining extra spaces
            property_name = ' '.join(property_name.split())
            
            scraped_data['property_name'] = property_name if property_name else "Unknown Property"
            
            # For location, create a clean official address format
            clean_address = full_address
            
            # Remove bedroom info and apartment/house prefixes from the beginning
            address_clean_patterns = [
                r'^Apartment\s+\d+\s+Bedroom\s*,?\s*',
                r'^House\s+\d+\s+Bedroom\s*,?\s*',  
                r'^Studio\s+Apartment\s*,?\s*',
                r'^\d+\s+Bedroom\s+Apartment\s*,?\s*',
                r'^\d+\s+Bedroom\s+House\s*,?\s*',
                r'^\d+\s+Bedroom\s*,?\s*',
                r'^\d+\s+Bed\s*,?\s*',
                r'^Studio\s*,?\s*',
                r'^Apartment\s*,?\s*',
                r'^House\s*,?\s*'
            ]
            
            for pattern in address_clean_patterns:
                clean_address = re.sub(pattern, '', clean_address, flags=re.IGNORECASE).strip()
            
            # Remove any leading symbols or special characters from address
            clean_address = re.sub(r'^[^\w\s]+', '', clean_address).strip()
            
            # Clean up any remaining extra commas or spaces at the beginning
            clean_address = re.sub(r'^,\s*', '', clean_address).strip()
            clean_address = re.sub(r'\s*,\s*', ', ', clean_address)  # Normalize comma spacing
            clean_address = ' '.join(clean_address.split())  # Normalize spaces
            
            scraped_data['location'] = clean_address if clean_address else full_address
            
            # Extract Dublin Zone from the full address
            dublin_zone = extract_dublin_zone(full_address)
            if dublin_zone:
                scraped_data['dublin_zone'] = dublin_zone
                
        except Exception: 
            scraped_data['property_name'] = "Unknown Property"
            scraped_data['location'] = "Location not found"
            
        # Extract Housing Type from URL or page content
        try:
            # First try to get from URL
            housing_type = None
            url_lower = url.lower()
            
            if 'studio' in url_lower:
                housing_type = 'Studio'
            elif '1-bedroom' in url_lower or '1bedroom' in url_lower:
                housing_type = '1 Bedroom'
            elif '2-bedroom' in url_lower or '2bedroom' in url_lower:
                housing_type = '2 Bedroom'
            elif '3-bedroom' in url_lower or '3bedroom' in url_lower or 'bedroom' in url_lower and '3' in url_lower:
                housing_type = '3 Bedroom+'
            
            # If not found in URL, try to get from beds element
            if not housing_type:
                try:
                    beds_element = driver.find_element(By.CSS_SELECTOR, '[data-testid="beds"]')
                    beds_text = beds_element.text.lower()
                    if 'studio' in beds_text:
                        housing_type = 'Studio'
                    elif '1' in beds_text and 'bed' in beds_text:
                        housing_type = '1 Bedroom'
                    elif '2' in beds_text and 'bed' in beds_text:
                        housing_type = '2 Bedroom'
                    elif '3' in beds_text and 'bed' in beds_text:
                        housing_type = '3 Bedroom+'
                except Exception:
                    pass
            
            if housing_type:
                scraped_data['housing_type'] = housing_type
                
        except Exception: pass

        # Extract Contact Information
        try:
            # Try multiple selectors for contact info
            contact_selectors = [
                '[data-testid="agent-name"]',
                '.agent-name',
                '[data-testid="contact-name"]',
                '.contact-name',
                '.agent-details h3',
                '.agent-details h4',
                '.contact-details h3',
                '.contact-details h4'
            ]
            
            contact_info = None
            for selector in contact_selectors:
                try:
                    contact_element = driver.find_element(By.CSS_SELECTOR, selector)
                    if contact_element.text.strip():
                        contact_info = contact_element.text.strip()
                        break
                except Exception:
                    continue
            
            # If still not found, try to find any element containing agent/contact info
            if not contact_info:
                try:
                    # Look for agent or contact information in various ways
                    potential_contacts = driver.find_elements(By.XPATH, "//*[contains(text(), 'Contact') or contains(text(), 'Agent')]/following-sibling::*")
                    for element in potential_contacts:
                        text = element.text.strip()
                        if text and len(text) > 2 and len(text) < 50:  # Reasonable name length
                            contact_info = text
                            break
                except Exception:
                    pass
            
            if contact_info:
                scraped_data['contact_info'] = contact_info
            else:
                scraped_data['contact_info'] = "Contact info not found"
                
        except Exception:
            scraped_data['contact_info'] = "Contact info not found"

    finally:
        driver.quit()

    return scraped_data

def parse_natural_date(date_input: str) -> str:
    """Parse natural language dates including relative dates like '3 days ago'."""
    if not date_input:
        return datetime.now().date().isoformat()
    
    # Use dateparser with settings to handle relative dates properly
    parsed = dateparser.parse(date_input, settings={
        "PREFER_DATES_FROM": "past",
        "RELATIVE_BASE": datetime.now()
    })
    
    if parsed:
        return parsed.date().isoformat()
    else:
        return datetime.now().date().isoformat()
        
def create_notion_page(**kwargs):
    """Creates a new page in the Notion database with dynamically built properties."""
    properties = {
        "Property Name": {
            "title": [{"text": {"content": kwargs.get("property_name", "Unknown Property")}}]
        },
        "Application Date": {
            "date": {"start": parse_natural_date(kwargs.get("application_date"))}
        },
        "Status": {
            "status": {"name": kwargs.get("status", "Applied")}
        }
    }

    if kwargs.get("website_link"):
        properties["Website Link"] = {"url": kwargs.get("website_link")}
    if kwargs.get("housing_type") and kwargs.get("housing_type") in HOUSING_TYPES:
        properties["Housing Type Needed"] = {"select": {"name": kwargs.get("housing_type")}}
    if kwargs.get("contact_info"):
        properties["Contact Information"] = {"rich_text": [{"text": {"content": kwargs.get("contact_info")}}]}
    if kwargs.get("location"):
        properties["Location"] = {"rich_text": [{"text": {"content": kwargs.get("location")}}]}
    if kwargs.get("price"):
        properties["Price"] = {"rich_text": [{"text": {"content": str(kwargs.get("price"))}}]}
    if kwargs.get("dublin_zone"):
        properties["Dublin Zone"] = {"rich_text": [{"text": {"content": kwargs.get("dublin_zone")}}]}

    notion.pages.create(parent={"database_id": DATABASE_ID}, properties=properties)

def update_notion_status(property_name: str, new_status: str) -> str:
    """Finds a page by property name and updates its status."""
    search_results = notion.databases.query(database_id=DATABASE_ID, filter={"property": "Property Name", "title": {"contains": property_name}})
    if not search_results["results"]:
        raise ValueError(f"No entry found with property name containing: {property_name}")
    page = search_results["results"][0]
    page_id = page["id"]
    title_data = page["properties"].get("Property Name", {}).get("title", [])
    full_property_name = title_data[0]["text"]["content"] if title_data else property_name
    notion.pages.update(page_id=page_id, properties={"Status": {"status": {"name": new_status} if new_status in STATUS_OPTIONS else {"name": "Applied"}}})
    return full_property_name

def get_filter_from_llm(nl_prompt: str) -> dict:
    """Converts a natural language prompt into a Notion filter and sort JSON object using an LLM."""
    prompt = f"""
    You are an expert system that converts natural language into a Notion API JSON payload.
    The user is querying a house application tracker database for housing in Dublin. Today is {today.isoformat()}.
    DATABASE SCHEMA:
    - "Property Name": (Title), "Application Date": (Date), "Housing Type Needed": (Select), "Status": (Status), "Location": (Rich Text), "Price": (Rich Text), "Dublin Zone": (Rich Text)
    Your task is to generate a JSON object with "filter" and "sorts" keys. The "sorts" should ALWAYS sort by "Application Date" in descending order.
    EXAMPLES:
    User: "What houses did I apply to last week?"
    Output: {{"filter": {{"and": [{{"property": "Application Date", "date": {{"on_or_after": "{last_week.isoformat()}"}}}}, {{"property": "Status", "status": {{"does_not_equal": "Not yet applied"}}}} ] }}, "sorts": [{{"property": "Application Date", "direction": "descending"}}]}}
    User: "show me applications in Dublin for under 2000 eur"
    Output: {{"filter": {{"and": [{{"property": "Location", "rich_text": {{"contains": "Dublin"}}}}, {{"property": "Price", "rich_text": {{"contains": "2000"}}}}, {{"property": "Price", "rich_text": {{"contains": "eur"}}}} ] }}, "sorts": [{{"property": "Application Date", "direction": "descending"}}]}}
    User: "show me applications in D1"
    Output: {{"filter": {{"property": "Dublin Zone", "rich_text": {{"contains": "D1"}}}}, "sorts": [{{"property": "Application Date", "direction": "descending"}}]}}
    Now, generate the JSON for the following user request. Only output the JSON object. User: "{nl_prompt}"
    """
    response = llm.invoke(prompt).content
    json_str = re.sub(r"```json|```", "", response.strip()).strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON. Raw output:\n{response}") from e

def query_notion_database(payload: dict) -> list:
    """Queries the Notion database using the provided filter and sort payload."""
    results = notion.databases.query(database_id=DATABASE_ID, **payload)
    records = []
    for item in results["results"]:
        props, record = item["properties"], {}
        for name, prop_data in props.items():
            prop_type = prop_data.get("type")
            value = None
            if prop_type == "title" and prop_data.get("title"): value = prop_data["title"][0]["text"]["content"]
            elif prop_type == "rich_text" and prop_data.get("rich_text"): value = prop_data["rich_text"][0]["text"]["content"]
            elif prop_type == "select" and prop_data.get("select"): value = prop_data["select"]["name"]
            elif prop_type == "status" and prop_data.get("status"): value = prop_data["status"]["name"]
            elif prop_type == "date" and prop_data.get("date"): value = prop_data["date"]["start"]
            elif prop_type == "url" and prop_data.get("url"): value = prop_data["url"]
            if value is not None: record[name] = value
        records.append(record)
    return records

def extract_date_from_text(text: str) -> str:
    """Extract date expressions from text like 'I applied 3 days ago'."""
    # Look for relative date patterns
    relative_patterns = [
        r'(\d+)\s+days?\s+ago',
        r'(\d+)\s+weeks?\s+ago', 
        r'yesterday',
        r'today',
        r'last\s+week',
        r'last\s+month'
    ]
    
    for pattern in relative_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    
    return None

def get_intent_and_payload(nl_prompt: str) -> dict:
    """Uses an LLM to determine intent and extract entities for manual input."""
    prompt = f"""
    You are an expert system that classifies a user's intent and extracts information for a house application tracker.
    Today is {today.isoformat()}. Yesterday was {yesterday.isoformat()}.
    Return JSON with "intent" and relevant fields: "property_name", "website_link", "application_date", "housing_type", "contact_info", "status", "location", "price", "dublin_zone".
    For "status", use one of: {', '.join(STATUS_OPTIONS)}. Default to "Applied". For "price", extract the currency and amount as a string.
    
    IMPORTANT: Pay special attention to date expressions like "3 days ago", "yesterday", "last week", etc. Extract these exactly as written.
    
    EXAMPLES:
    Input: "I applied yesterday to 17 Spencer House, Custom House Square, Mayor Street Lower, IFSC, Dublin 1 for eur 1772 per month for 1 bedroom."
    Output: {{"intent": "create", "property_name": "17 Spencer House", "location": "Custom House Square, Mayor Street Lower, IFSC, Dublin 1", "price": "EUR 1772 per month", "housing_type": "1 Bedroom", "status": "Applied", "application_date": "yesterday", "dublin_zone": "D1"}}
    
    Input: "I applied to Sunset Apartments 3 days ago for a 1 bedroom"
    Output: {{"intent": "create", "property_name": "Sunset Apartments", "housing_type": "1 Bedroom", "status": "Applied", "application_date": "3 days ago"}}
    
    Input: "I applied to https://www.daft.ie/for-rent/apartment-17-spencer-house-custom-house-square-mayor-street-lower-ifsc-dublin-1/6230870 3 days ago"
    Output: {{"intent": "create", "website_link": "https://www.daft.ie/for-rent/apartment-17-spencer-house-custom-house-square-mayor-street-lower-ifsc-dublin-1/6230870", "status": "Applied", "application_date": "3 days ago"}}
    
    Input: "Oak Street House rejected my application"
    Output: {{"intent": "update", "property_name": "Oak Street House", "status": "Rejected"}}
    
    Input: "show me all my accepted applications"
    Output: {{"intent": "query"}}
    
    Now, classify the intent and extract the fields for the following input. Only output the JSON object. Input: "{nl_prompt}"
    """
    response = llm.invoke(prompt).content.strip()
    return json.loads(re.sub(r"```json|```", "", response).strip())

# --- 🖼️ STREAMLIT UI 🖼️ ---

st.set_page_config(page_title="Notion House Tracker AI", layout="centered")
st.title("🏠 House Application Tracker")
st.markdown("Track your house application here Kanojo~")

with st.expander("💡 Example Commands"):
    st.markdown("""
    **Add via Link:** `"Applied to https://www.daft.ie/for-rent/apartment-1-bedroom-griffith-wood-griffith-wood-griffith-avenue-drumcondra-dublin-9/3523579"`  
    **Add via Link with Date:** `"Applied to https://www.daft.ie/for-rent/... 3 days ago"`  
    **Add Manually:** `"I applied to Sunset Apartments for 2200 per month 3 days ago"`  
    **Update:** `"Maple Gardens rejected my application"`  
    **Query:** `"Show me all accepted applications"` or `"Show me applications in D1"`
    """)

with st.form("notion_form"):
    nl_prompt = st.text_input("💬 What would you like to do?", placeholder="Paste a daft.ie link or type your request...")
    submitted = st.form_submit_button("Enter ", use_container_width=True)

if submitted and nl_prompt:
    if not all([DATABASE_ID, st.secrets.get("NOTION_API_KEY"), st.secrets.get("OPENAI_API_KEY")]):
        st.error("❌ CONFIGURATION ERROR: Please set your DATABASE_ID, NOTION_API_KEY, and OPENAI_API_KEY as Secrets in Streamlit Cloud.")
    else:
        try:
            # Check for a daft.ie URL first
            url_match = re.search(r"https?://(www\.)?daft\.ie/[^\s]+", nl_prompt)

            if url_match:
                url = url_match.group(0).strip('.') # Clean any trailing punctuation
                
                # Extract date from the text if present
                date_from_text = extract_date_from_text(nl_prompt)
                
                with st.spinner(f"🔍 Scraping {url}..."):
                    scraped_data = scrape_daft_ie(url)
                
                if not scraped_data:
                    st.warning("Could not extract details from the website. It might be an unsupported page format.")
                else:
                    # Set the website link to just the URL (not the full input text)
                    scraped_data['website_link'] = url
                    scraped_data['status'] = 'Applied'
                    
                    # Use the date from text if available, otherwise default to today
                    if date_from_text:
                        scraped_data['application_date'] = date_from_text
                    else:
                        scraped_data['application_date'] = 'today'
                    
                    with st.spinner("✍️ Creating entry in Notion..."):
                        create_notion_page(**scraped_data)
                    
                    # Create success message with key extracted info
                    zone_info = f" in {scraped_data['dublin_zone']}" if scraped_data.get('dublin_zone') else ""
                    date_info = f" (applied {scraped_data.get('application_date', 'today')})" if scraped_data.get('application_date') else ""
                    
                    st.success(f"✅ Created application for **{scraped_data.get('property_name', 'Unknown Property')}**{zone_info}{date_info}!")

            else:
                # If no URL, use the original AI-based logic
                with st.spinner("Kareshi is thinking..."):
                    action = get_intent_and_payload(nl_prompt)
                    intent = action.get("intent")

                if intent == "query":
                    with st.spinner("🔍 Searching Notion..."):
                        notion_payload = get_filter_from_llm(nl_prompt)
                        records = query_notion_database(notion_payload)
                    st.success(f"Found **{len(records)}** application(s).")
                    if records: st.dataframe(records, use_container_width=True)

                elif intent == "create":
                    with st.spinner("✍️ Creating entry in Notion..."):
                        create_notion_page(**action)
                    
                    zone_info = f" in {action.get('dublin_zone')}" if action.get('dublin_zone') else ""
                    date_info = f" (applied {action.get('application_date')})" if action.get('application_date') and action.get('application_date') != datetime.now().date().isoformat() else ""
                    st.success(f"✅ Application for **{action.get('property_name')}**{zone_info}{date_info} has been created!")

                elif intent == "update":
                    with st.spinner("🔄 Updating status in Notion..."):
                        full_name = update_notion_status(action["property_name"], action["status"])
                    st.success(f"✅ Status for **{full_name}** updated to **{action['status']}**!")
                else:
                    st.warning("⚠️ Could not determine your intent. Please try rephrasing.")

        except Exception as e:
            st.error(f"❌ An error occurred: {e}")
            st.info("Please check your Notion Database ID, API keys, and that the integration is shared with the database.")

st.markdown("---")
st.markdown("<div style='text-align: center;'>I love you bb</div>", unsafe_allow_html=True)
