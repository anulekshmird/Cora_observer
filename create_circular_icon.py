import os
from PIL import Image, ImageDraw, ImageOps

def create_circular_icon():
    # Define paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    source_path = os.path.join(base_dir, "icons", "new_icon_source.jpg")
    target_path = os.path.join(base_dir, "icon.png")
    
    if not os.path.exists(source_path):
        print(f"Error: Source image not found at {source_path}")
        # Validating fallback
        source_path = os.path.join(base_dir, "new_icon_source.jpg")
        if not os.path.exists(source_path):
             print(f"Error: Source image not found at fallback {source_path}")
             return

    print(f"Processing {source_path}...")
    
    try:
        img = Image.open(source_path).convert("RGBA")
        
        # Create a circular mask
        size = (min(img.size), min(img.size))
        mask = Image.new('L', size, 0)
        draw = ImageDraw.Draw(mask) 
        draw.ellipse((0, 0) + size, fill=255)
        
        output = ImageOps.fit(img, mask.size, centering=(0.5, 0.5))
        output.putalpha(mask)
        
        # Resize for high quality (e.g. 128x128)
        output = output.resize((128, 128), Image.Resampling.LANCZOS)
        
        output.save(target_path, "PNG")
        print(f"Successfully saved circular icon to {target_path}")
        
    except Exception as e:
        print(f"Failed to process icon: {e}")

if __name__ == "__main__":
    create_circular_icon()
