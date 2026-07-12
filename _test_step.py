"""Find where the hang is"""
import sys, time
sys.path.insert(0, '.')

print('Step 1: importing bus_service...', end=' ')
sys.stdout.flush()
start = time.time()
import bus_service
print(f'done ({time.time()-start:.2f}s)')

print('Step 2: importing pandas...', end=' ')
sys.stdout.flush()
import pandas as pd
print('done')

print('Step 3: reading Excel...', end=' ')
sys.stdout.flush()
start = time.time()
df = pd.read_excel('bus_data.xlsx')
stop_buses = df[df['站点编号'] == 'M02']
print(f'done ({time.time()-start:.2f}s), rows={len(stop_buses)}')

print('Step 4: calling fetch_bus_data...', end=' ')
sys.stdout.flush()
start = time.time()
result = bus_service.fetch_bus_data('M02')
print(f'done ({time.time()-start:.2f}s)')
print(f'Source: {result.get("数据来源")}')
