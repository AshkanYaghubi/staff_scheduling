import pandas as pd
from sqlalchemy import create_engine, Integer, String, Float, Date, Time

# Load CSV from URL
url = "concat_invoice.csv"
df = pd.read_csv(url)

# The dataframe already has your columns:
# billid, GoodsName, GoodsCount, GoodsCode, InvoiceDate, InvoiceTime, BranchId, BranchName

# Create database connection
engine = create_engine('postgresql://root:oynn9r0O2KJ60NeTwYtzcHNK@rainier.liara.cloud:32419/postgres')

# Create table and insert data in one step
df.to_sql('schedule.sales', engine, if_exists='replace', index=False)

print("Table created and data inserted successfully!")
