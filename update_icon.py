from PIL import Image

def process_icon():
    source = "new_icon_source.jpg"
    target = "icon.png"
    
    try:
        img = Image.open(source)
        # Convert to RGBA for transparency support if we wanted it, 
        # but since it's a square jpg, we'll just keep it square per user request ("icon given in the second image")
        # Actually user said "remove background" for previous one, but this one looks like a cool square bubble.
        # Let's keep it as is, or maybe round corners?
        # User said "icon given in the second image".
        
        img = img.resize((64, 64), Image.Resampling.LANCZOS)
        img.save(target, "PNG")
        print("Icon updated.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    process_icon()
