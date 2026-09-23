from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ضياء بوت</title>
    </head>

    <body style="
        margin: 0;
        background: #0d0915;
        color: white;
        font-family: Arial, sans-serif;
        text-align: center;
    ">

        <div style="padding: 100px 20px;">
            <h1 style="font-size: 45px; color: #b66cff;">
                ضياء BOT
            </h1>

            <p style="font-size: 20px; color: #cccccc;">
                أهلاً بك في الموقع الرسمي للبوت 🤖
            </p>
        </div>

    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080
    )
