# Genera icono.ico: hoja con un trazo de pincel rosa y una flecha de "sacar"
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, S - 16, S - 16), radius=44, fill=(43, 40, 56, 255))
d.rounded_rectangle((52, 40, 204, 216), radius=14, fill=(250, 248, 252, 255))
d.line([(76, 170), (110, 110), (140, 140), (180, 76)], fill=(244, 108, 150, 255), width=18, joint="curve")
d.polygon([(150, 178), (196, 178), (173, 214)], fill=(70, 160, 240, 255))
d.rectangle((164, 140, 182, 180), fill=(70, 160, 240, 255))
img.save("icono.ico", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
img.save("icono.png")
