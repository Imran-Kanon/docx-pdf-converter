import os
import re
import subprocess
import tempfile
from pathlib import Path
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

@app.get("/")
def home():
    return """
<!doctype html>
<html lang="bn">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DOCX থেকে PDF</title>
<style>
body {
  font-family: sans-serif;
  max-width: 600px;
  margin: 35px auto;
  padding: 18px;
  background: #f5f1eb;
  color: #30271f;
}
main {
  background: white;
  padding: 24px;
  border-radius: 14px;
}
h2 { color: #79552e; }
input, button {
  box-sizing: border-box;
  width: 100%;
  padding: 13px;
  margin-top: 14px;
}
button {
  background: #79552e;
  color: white;
  border: 0;
  border-radius: 8px;
  font-size: 16px;
}
#status { white-space: pre-wrap; margin-top: 18px; }
small { color: #665e56; }
</style>
</head>
<body>
<main>
<h2>DOCX থেকে PDF কনভার্টার</h2>
<p>আপনার Word ফাইল নির্বাচন করে PDF তৈরি করুন।</p>
<form id="form">
<input type="file" id="file" accept=".docx" required>
<button type="submit">PDF তৈরি করুন</button>
</form>
<div id="status"></div>
<small>ফাইলের সর্বোচ্চ আকার: ৫০ MB। বড় ফাইলে সময় বেশি লাগতে পারে।</small>
</main>
<script>
const form = document.getElementById("form");
const statusBox = document.getElementById("status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const file = document.getElementById("file").files[0];
  if (!file) return;

  if (!file.name.toLowerCase().endsWith(".docx")) {
    statusBox.textContent = "দয়া করে একটি DOCX ফাইল নির্বাচন করুন।";
    return;
  }

  const data = new FormData();
  data.append("file", file);

  statusBox.textContent = "কনভার্ট হচ্ছে। অপেক্ষা করুন...";

  try {
    const response = await fetch("/convert", {
      method: "POST",
      body: data
    });

    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "কনভার্ট করা যায়নি।");
    }

    const embedding = response.headers.get("X-Font-Embedding-Status");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = url;
    link.download = file.name.replace(/\\.docx$/i, "") + ".pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    if (embedding === "yes") {
      statusBox.textContent =
        "PDF তৈরি হয়েছে এবং পরীক্ষিত PDF-তে ব্যবহৃত ফন্টগুলো এমবেড করা আছে।";
    } else if (embedding === "no") {
      statusBox.textContent =
        "PDF তৈরি হয়েছে, কিন্তু অন্তত একটি ফন্ট এমবেড করা নেই।";
    } else {
      statusBox.textContent =
        "PDF তৈরি হয়েছে। ফন্ট এমবেডের অবস্থা নিশ্চিতভাবে যাচাই করা যায়নি।";
    }
  } catch (error) {
    statusBox.textContent = "সমস্যা: " + error.message;
  }
});
</script>
</body>
</html>
"""


@app.post("/convert")
def convert():
    uploaded = request.files.get("file")

    if not uploaded or not uploaded.filename:
        return jsonify(error="DOCX ফাইল নির্বাচন করুন।"), 400

    if not uploaded.filename.lower().endswith(".docx"):
        return jsonify(error="শুধু DOCX ফাইল গ্রহণ করা হয়।"), 400

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        source = work / "input.docx"
        output_dir = work / "output"
        profile_dir = work / "lo-profile"
        output_dir.mkdir()

        uploaded.save(source)

        try:
            subprocess.run(
                [
                    "libreoffice",
                    "-env:UserInstallation=" + profile_dir.as_uri(),
                    "--headless",
                    "--convert-to", "pdf",
                    "--outdir", str(output_dir),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            )
        except subprocess.TimeoutExpired:
            return jsonify(error="কনভার্ট করতে বেশি সময় লাগছে। ছোট ফাইল দিয়ে চেষ্টা করুন।"), 504
        except subprocess.CalledProcessError:
            return jsonify(error="DOCX পড়া যায়নি। ফাইলটি ঠিক আছে কি না পরীক্ষা করুন।"), 422
        except Exception:
            app.logger.exception("Conversion failed")
            return jsonify(error="সার্ভারে কনভার্ট করার সমস্যা হয়েছে।"), 500

        pdf = output_dir / "input.pdf"

        if not pdf.is_file() or pdf.stat().st_size == 0:
            return jsonify(error="PDF তৈরি হয়নি। অন্য DOCX দিয়ে চেষ্টা করুন।"), 500

        embedding_status = "unknown"

        try:
            check = subprocess.run(
                ["pdffonts", str(pdf)],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )

            lines = check.stdout.splitlines()[2:]
            font_flags = []

            for line in lines:
                match = re.search(
                    r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$",
                    line,
                )
                if match:
                    font_flags.append(match.group(1))

            if font_flags:
                embedding_status = (
                    "yes" if all(flag == "yes" for flag in font_flags)
                    else "no"
                )
        except Exception:
            app.logger.warning("Could not verify PDF font embedding")

        response = send_file(
            pdf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=Path(uploaded.filename).stem + ".pdf",
        )
        response.headers["X-Font-Embedding-Status"] = embedding_status
        return response


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="ফাইলটি ৫০ MB-এর চেয়ে বড়।"), 413
