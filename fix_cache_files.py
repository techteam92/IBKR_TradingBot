"""
Quick fix script to delete old cache files that may cause compatibility issues
Run this before starting the application if you get numpy.core.multiarray errors
"""

import os

files_to_delete = ['Cache.npy', 'Settings.npy']

print("=" * 60)
print("TWS Trading GUI - Cache File Cleanup")
print("=" * 60)
print()

for file in files_to_delete:
    if os.path.exists(file):
        try:
            os.remove(file)
            print(f"✓ Deleted: {file}")
        except Exception as e:
            print(f"✗ Could not delete {file}: {e}")
    else:
        print(f"- Not found: {file} (this is OK)")

print()
print("=" * 60)
print("Cache files cleaned!")
print("You can now start the application with fresh settings.")
print("=" * 60)
print()
input("Press Enter to exit...")



