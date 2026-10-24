from pathlib import Path
import pandas as pd
import requests

# 1. Boston Open Data API endpoint for 911 dispatch records
resource_id = "e4bfe397-6bfc-49c5-9367-c879fac7401d"
url = f"https://data.boston.gov/api/3/action/datastore_search?resource_id={resource_id}&limit=1000"

print("Fetching data from Boston Open Data Portal...")
response = requests.get(url)
response.raise_for_status()

# 2. Parse the JSON response
data = response.json()
records = data["result"]["records"]

# 3. Locate the script's directory and set output path
script_dir = Path(__file__).parent
output_file = script_dir / "boston_dispatch_data.csv"

# 4. Save to CSV
df = pd.DataFrame(records)
df.to_csv(output_file, index=False)

print(f"Successfully exported {len(df)} records to: {output_file}")