from PIL import Image
import shutil
import os

def remove_white_background():
    source = r"C:/Users/ADITHYA/.gemini/antigravity/brain/5dab5629-5beb-4589-8d94-b5aacada887a/uploaded_media_1770360761473.png"
    target = r"c:/Users/ADITHYA/OneDrive/Desktop/Cora antig/cora/icon.png"
    
    print(f"Processing {source}...")
    
    img = Image.open(source)
    img = img.convert("RGBA")
    
    datas = img.getdata()
    
    new_data = []
    for item in datas:
        # Change all white (also shades of whites) to transparent
        # Threshold: > 240 for R, G, B
        if item[0] > 240 and item[1] > 240 and item[2] > 240:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)
    
    img.putdata(new_data)
    img.save(target, "PNG")
    print(f"Saved transparent icon to {target}")

if __name__ == "__main__":
    remove_white_background()
