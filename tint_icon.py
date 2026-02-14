from PIL import Image

def tint_icon(path, color=(255, 255, 255)):
    try:
        img = Image.open(path).convert("RGBA")
        datas = img.getdata()
        new_data = []
        for item in datas:
            if item[3] > 0: # If not transparent
                new_data.append((color[0], color[1], color[2], item[3]))
            else:
                new_data.append(item)
        img.putdata(new_data)
        img.save(path)
        print(f"Tinted {path} to white.")
    except Exception as e:
        print(f"Failed to tint icon: {e}")

if __name__ == "__main__":
    tint_icon("icons/mic.png")
