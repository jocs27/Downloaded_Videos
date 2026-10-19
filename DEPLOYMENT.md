# Public deployment

GitHub Pages only serves static files; it cannot run this Python downloader. The included Render Docker service runs the website and downloader together, so it can be opened from a phone or computer.

## Deploy to Render

1. Create a GitHub repository and push `yt_downloader.py`, `Dockerfile`, `requirements.txt`, and `render.yaml` to it.
2. In Render, create a new Blueprint and connect that repository. Render reads `render.yaml` and builds the Docker service.
3. When prompted for `APP_PASSWORD`, set a unique password. Do not put it in GitHub or share it in chat. The site uses HTTP Basic Authentication; enter `downloader` as the username and the value you configured as the password.
4. Open the Render service URL on your phone. After a download finishes, tap **Download to this device** to save it through the browser.

The Blueprint uses Render's free plan. Free services can sleep while idle, and their local disk is temporary; completed files and download links may disappear after a restart or redeploy. The recent-download history is saved in each browser and is not shared between devices. For an always-on service or durable server-side file storage, configure a paid Render plan and persistent storage before relying on it.
