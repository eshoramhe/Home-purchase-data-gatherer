import streamlit as st
import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
import pandas as pd # Import pandas for tabular data

# Firebase imports
from firebase_admin import initialize_app, credentials
from firebase_admin import firestore
from firebase_admin import auth

# --- Initialize Firebase (MANDATORY for persistence) ---
# Global variables from Canvas environment
# Check if __app_id is defined and not None, otherwise use 'default-app-id'
app_id = typeof __app_id !== 'undefined' && __app_id is not None ? __app_id : 'default-app-id'
# Check if __firebase_config is defined and not None, otherwise use empty dict
firebase_config = JSON.parse(typeof __firebase_config !== 'undefined' && __firebase_config is not None ? __firebase_config : '{}')
# Check if __initial_auth_token is defined and not None, otherwise use None
initial_auth_token = typeof __initial_auth_token !== 'undefined' && __initial_auth_token is not None ? __initial_auth_token : None


# Initialize Firebase app only once
if not st.session_state.get('firebase_initialized', False):
    try:
        # Check if Firebase has already been initialized to avoid error
        # This is a common pattern in Streamlit to handle reruns
        if not firestore._apps: # Check if there are any initialized apps
            # Initialize with credentials and a name derived from app_id
            cred = credentials.Certificate(firebase_config)
            initialize_app(cred, name=app_id)
        
        # Get the Firestore client for the specific app
        db = firestore.client(app=firestore.get_app(name=app_id))
        st.session_state.db = db # Store db client in session state

        # Authenticate user
        if initial_auth_token:
            # Use the provided custom auth token for Canvas environment
            user = auth.verify_id_token(initial_auth_token)
            st.session_state.user_id = user['uid']
        else:
            # Fallback for anonymous sign-in if no auth token (e.g., local development outside Canvas)
            # In a real deployed Canvas, __initial_auth_token should always be present
            # Generate a random UUID for anonymous users for unique session identification
            st.session_state.user_id = "anonymous_" + str(uuid.uuid4()) # Added uuid import
            
        st.session_state.firebase_initialized = True
        st.session_state.auth_ready = True # Indicate authentication is ready
    except Exception as e:
        st.error(f"Error initializing Firebase: {e}")
        st.session_state.firebase_initialized = False
        st.session_state.auth_ready = False


# --- Web Page Reader App Title and Description ---
st.set_page_config(
    page_title="Web Page Data Extractor",
    page_icon="🏠",
    layout="centered"
)

st.title("🏠 Web Page Data Extractor App")

st.write(
    """
    Enter a URL of a real estate listing below. The app will attempt to extract
    key information such as address, cost, square footage, number of bedrooms,
    and publication date. All gathered data will be saved to your personal data store.
    """
)

# --- Function to extract real estate data ---
def extract_real_estate_data(soup, url_input): # Pass url_input to the function
    data = {
        "Address": None,
        "Cost to Buy": None,
        "Cost to Rent": None,
        "House Square Footage": None,
        "Plot Square Footage": None,
        "Number of Bedrooms": None,
        "Publication Date": None,
        "Extracted URL": url_input, # Store the URL for which data was extracted
        "Extraction Timestamp": datetime.now().isoformat() # Add a timestamp
    }

    # --- Address ---
    # Common patterns for address
    address_tags = [
        soup.find('h1', class_=re.compile(r'address|property-address', re.I)),
        soup.find('span', itemprop='streetAddress'),
        soup.find('div', class_=re.compile(r'address', re.I)),
        soup.find('meta', property='og:street-address')
    ]
    for tag in address_tags:
        if tag and tag.get_text(strip=True):
            data["Address"] = tag.get_text(strip=True)
            break
        elif tag and tag.get('content'):
            data["Address"] = tag.get('content')
            break

    # --- Costs (Buy/Rent) ---
    # Look for price-like elements
    price_elements = soup.find_all(text=re.compile(r'\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?|R\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?', re.I)) # Matches $ or R followed by numbers
    for element in price_elements:
        parent_text = element.parent.get_text(strip=True)
        if "buy" in parent_text.lower() or "for sale" in parent_text.lower() or "price" in parent_text.lower():
            if not data["Cost to Buy"]:
                data["Cost to Buy"] = element.strip()
        elif "rent" in parent_text.lower() or "per month" in parent_text.lower():
            if not data["Cost to Rent"]:
                data["Cost to Rent"] = element.strip()
        # Fallback if no specific indicator but a price is found
        if not data["Cost to Buy"] and not data["Cost to Rent"] and len(element.strip()) > 3: # Avoid very short numbers
             if any(kw in parent_text.lower() for kw in ["cost", "value", "price", "amount"]):
                 if not data["Cost to Buy"] and not data["Cost to Rent"]: # Only set if both are still None
                     data["Cost to Buy"] = element.strip() # Default to buy if ambiguous


    # --- Square Footage (House & Plot) ---
    # Look for patterns like "1,200 sqft", "1,500 sq. ft.", "10,000 m²"
    sqft_regex = r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(sq\.?\s*ft\.?|m\u00B2|m2|square\s*feet|sqft)' # \u00B2 is unicode for ²
    
    # Search within common property details containers
    detail_containers = soup.find_all(['span', 'div', 'li', 'p'], class_=re.compile(r'area|size|details|specs', re.I))
    all_text = " ".join([cont.get_text(strip=True) for cont in detail_containers]) or soup.get_text()

    # Prefer specific keywords for house vs. plot
    house_match = re.search(r'(?:house|home|living|interior|building)\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(sq\.?\s*ft\.?|m\u00B2|m2|square\s*feet|sqft)', all_text, re.I)
    plot_match = re.search(r'(?:lot|plot|land|property|garden)\s*size\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(sq\.?\s*ft\.?|m\u00B2|m2|square\s*feet|sqft)', all_text, re.I)
    
    if house_match:
        data["House Square Footage"] = f"{house_match.group(1)} {house_match.group(2).replace('sq. ft.', 'sqft')}"
    
    if plot_match:
        data["Plot Square Footage"] = f"{plot_match.group(1)} {plot_match.group(2).replace('sq. ft.', 'sqft')}"

    # General square footage if specific matches not found
    if not data["House Square Footage"] and not data["Plot Square Footage"]:
        general_sqft_matches = re.finditer(sqft_regex, all_text, re.I)
        for match in general_sqft_matches:
            val = f"{match.group(1)} {match.group(2).replace('sq. ft.', 'sqft')}"
            if "house" in match.string.lower() or "interior" in match.string.lower():
                data["House Square Footage"] = val
            elif "lot" in match.string.lower() or "land" in match.string.lower():
                data["Plot Square Footage"] = val
            elif not data["House Square Footage"]: # Default to house if ambiguous
                data["House Square Footage"] = val
            elif not data["Plot Square Footage"]: # Then to plot if still ambiguous
                data["Plot Square Footage"] = val


    # --- Number of Bedrooms ---
    # Look for patterns like "3 beds", "4 bd", "2 bedrooms"
    bedroom_regex = r'(\d+)\s*(?:beds?|bedrooms?|br\b)'
    bedroom_tags = soup.find_all(text=re.compile(bedroom_regex, re.I))
    for tag in bedroom_tags:
        match = re.search(bedroom_regex, tag, re.I)
        if match:
            data["Number of Bedrooms"] = int(match.group(1))
            break
    
    # Fallback: check common metadata or attributes
    if not data["Number of Bedrooms"]:
        meta_bedrooms = soup.find('meta', property='og:beds')
        if meta_bedrooms and meta_bedrooms.get('content'):
            try:
                data["Number of Bedrooms"] = int(float(meta_bedrooms.get('content')))
            except ValueError:
                pass


    # --- Publication Date ---
    # Look for <time> tags with datetime attribute
    time_tag = soup.find('time')
    if time_tag and time_tag.get('datetime'):
        try:
            # Attempt to parse ISO format first
            dt_obj = datetime.fromisoformat(time_tag['datetime'].replace('Z', '+00:00'))
            data["Publication Date"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            # Fallback for other date formats in datetime attribute
            data["Publication Date"] = time_tag['datetime']
    elif time_tag and time_tag.get_text(strip=True):
        # If no datetime attribute, try to get text and parse
        date_text = time_tag.get_text(strip=True)
        # Attempt to parse common date formats
        try:
            # Example: "Posted on January 15, 2023"
            match = re.search(r'(?:posted on|published on|listed on)\s*(.*)', date_text, re.I)
            if match:
                parsed_date = datetime.strptime(match.group(1), "%B %d, %Y")
                data["Publication Date"] = parsed_date.strftime("%Y-%m-%d")
            else:
                data["Publication Date"] = date_text # As a last resort, just save the text
        except ValueError:
            data["Publication Date"] = date_text # If parsing fails, keep original text
            

    return data

# --- Input for URL ---
st.header("Enter Web Page URL")

url_input = st.text_input(
    "URL",
    value="",
    placeholder="e.g., https://www.zillow.com/homedetails/...",
    key="url_input"
)

# --- Check Firebase and Auth status ---
if not st.session_state.get('auth_ready', False):
    st.info("Initializing application services... Please wait.")
    st.stop() # Halt execution until Firebase is ready

db = st.session_state.db
user_id = st.session_state.user_id

# Display the user ID for debugging/identification (Mandatory for multi-user apps)
st.write(f"Logged in as User ID: `{user_id}`")


# --- Read Web Page Button ---
if st.button("Extract Real Estate Data", use_container_width=True, type="primary"):
    if not url_input:
        st.warning("Please enter a URL to extract data from.")
    else:
        with st.spinner("Fetching and extracting data..."):
            try:
                # Add a user-agent header to mimic a browser
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                
                # Fetch the content of the URL
                response = requests.get(url_input, headers=headers, timeout=10)
                response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)

                # Parse the HTML content using BeautifulSoup
                soup = BeautifulSoup(response.text, 'html.parser')

                # Extract data, passing url_input to the function
                extracted_data = extract_real_estate_data(soup, url_input)
                
                # Check if any non-URL data was extracted
                if any(value is not None for key, value in extracted_data.items() if key not in ["Extracted URL", "Extraction Timestamp"]):
                    st.subheader("Newly Extracted Data:")
                    # Convert the extracted dictionary to a DataFrame for tabular display
                    # Create a list containing the single dictionary
                    df_new = pd.DataFrame([extracted_data]) 
                    st.dataframe(df_new) # Display the newly extracted data as a table

                    # Save to Firestore
                    try:
                        # Collection path for private user data
                        collection_ref = db.collection(f'artifacts/{app_id}/users/{user_id}/home_data')
                        collection_ref.add(extracted_data)
                        st.success("Data successfully saved to your personal storage!")
                    except Exception as e:
                        st.error(f"Failed to save data to Firestore: {e}")
                else:
                    st.warning("Could not extract specific real estate data from this page. "
                               "The page structure might be different or the data is not present.")
                    st.info("You can try inspecting the page's HTML to understand its structure.")
                    st.text_area("Raw HTML (for debugging)", response.text, height=300, disabled=True)


            except requests.exceptions.MissingSchema:
                st.error("Error: Invalid URL format. Please include 'http://' or 'https://'.")
            except requests.exceptions.ConnectionError:
                st.error("Error: Could not connect to the website. Please check your internet connection or if the URL is correct.")
            except requests.exceptions.Timeout:
                st.error("Error: The request timed out. The server might be slow or the URL is unreachable.")
            except requests.exceptions.HTTPError as e:
                st.error(f"Error fetching the URL (HTTP {e.response.status_code}): {e}. The server denied access or the page does not exist.")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")

st.markdown("---")
st.header("All Saved Home Data")

# --- Fetch and Display All Saved Data from Firestore ---
if st.session_state.get('auth_ready', False):
    try:
        # Fetch all documents from the user's home_data collection
        collection_ref = db.collection(f'artifacts/{app_id}/users/{user_id}/home_data')
        docs = collection_ref.stream() # Get all documents

        all_home_data = []
        for doc in docs:
            doc_data = doc.to_dict()
            all_home_data.append(doc_data)

        if all_home_data:
            df_all = pd.DataFrame(all_home_data)
            st.dataframe(df_all)

            # Provide download button for ALL data as CSV
            csv_output_all = df_all.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download All Saved Data as CSV",
                data=csv_output_all,
                file_name="all_home_data.csv", # Changed filename to reflect all data
                mime="text/csv"
            )
        else:
            st.info("No home data saved yet. Enter a URL and extract some data!")
    except Exception as e:
        st.error(f"Error fetching saved data: {e}")
else:
    st.info("Waiting for Firebase to initialize before loading saved data.")


st.markdown("---")
st.info("Developed with Streamlit and Firebase for persistent data storage.")
