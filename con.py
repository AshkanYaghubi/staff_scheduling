import pandas as pd

# Read tables from Excel file
df_original = pd.read_csv('concat_invoice.csv')  # or sheet_name=0 for first sheet
df_table2 = pd.read_excel('time.xlsx', sheet_name=0)  # specify the sheet name

# Alternative if tables are in the same Excel file but different sheets:
# df_original = pd.read_excel('your_file.xlsx', sheet_name='Sheet1')
# df_table2 = pd.read_excel('your_file.xlsx', sheet_name='Sheet2')

# Ensure Time column is float
df_table2['Time'] = df_table2['Time'].astype(float)

# Merge to add the Time column
df_result = df_original.merge(
    df_table2[['GoodsCode', 'Time']],
    on='GoodsCode',
    how='left'
)

# Set default value for missing times (convert to float)
DEFAULT_TIME = 3.5
df_result['Time'] = df_result['Time'].fillna(DEFAULT_TIME)

# Ensure Time is float (in case of any other data types)
df_result['Time'] = df_result['Time'].astype(float)

# Calculate total time
df_result['TotalTime'] = df_result['Time'] * df_result['GoodsCount']


df_result['InvoiceHour'] = pd.to_datetime(df_result['InvoiceTime'], format='%H:%M:%S').dt.hour

# Group by Branch, Date, and Hour, then sum TotalTime
hourly_summary = df_result.groupby(['BranchName', 'InvoiceDate', 'InvoiceHour'])['TotalTime'].sum().reset_index()

# Rename column for clarity
hourly_summary = hourly_summary.rename(columns={'TotalTime': 'TotalMinutesInHour'})

# Save to new Excel file if needed
hourly_summary.to_csv('output_file.csv', index=False)

print(hourly_summary.head())
print(f"\nData types:\n{df_result.dtypes}")
