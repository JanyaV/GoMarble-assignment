import os
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from groq import Groq
import json
import time
import logging

# Initialize FastAPI app
app = FastAPI()

# Groq API Configuration
GROQ_API_KEY = "g##########n6hnP#########gW1i0W#####2qw"  # Replace with your actual API key
client = Groq(api_key=GROQ_API_KEY)

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger()

# Response model for API
class ReviewResponse(BaseModel):
    reviews_count: int
    reviews: list[dict]

# Function to fetch reviews using Selenium and Groq API
def fetch_reviews(url: str):
    try:
        # Selenium setup
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.get(url)
        time.sleep(5)  # Wait for the page to fully load
        html_content = driver.page_source
        driver.quit()

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        reviews = []

        # Attempt to extract reviews heuristically
        for review_block in soup.find_all(text=lambda t: "reviews" in t.lower() or "customer" in t.lower()):
            parent = review_block.find_parent()
            if parent:
                title = parent.find_next("h1") or parent.find_next("h2") or parent.find_next("span")
                rating = parent.find_next("span", class_=lambda x: x and "rating" in x)
                reviewer = parent.find_next("div", class_=lambda x: x and "reviewer" in x)
                body = parent.find_next("p")

                reviews.append({
                    "title": title.get_text(strip=True) if title else None,
                    "rating": rating.get_text(strip=True) if rating else None,
                    "reviewer": reviewer.get_text(strip=True) if reviewer else None,
                    "body": body.get_text(strip=True) if body else None
                })

        # Additional Groq-based refinement
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Correct Groq model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {
                    "role": "user",
                    "content": f"Extract CSS selectors for reviews, titles, ratings, reviewers, user Rating or anything related to a review of  a or any product from the following HTML: {html_content[:3000]}"
                }
            ],
            stream=False,
            max_tokens=1024
        )

        logger.info(f"Groq API Response: {response}")

        # Parse Groq API response for selectors
        selectors_text = response.choices[0].message.content
        try:
            selectors = json.loads(selectors_text)
        except json.JSONDecodeError:
            logger.warning("Groq API provided non-JSON suggestions for CSS selectors.")
            selectors = {}

        # Use Groq-provided selectors if available
        required_keys = ["review", "title", "body", "rating", "reviewer", "images"]
        if all(key in selectors for key in required_keys):
            for review in soup.select(selectors["review"]):
                try:
                    title = review.select_one(selectors["title"]).get_text(strip=True)
                    body = review.select_one(selectors["body"]).get_text(strip=True)
                    rating = float(review.select_one(selectors["rating"]).get_text(strip=True))
                    reviewer = review.select_one(selectors["reviewer"]).get_text(strip=True)
                    images = [
                        img["src"]
                        for img in review.select(selectors["images"])
                        if img.get("src")
                    ]
                    reviews.append({
                        "title": title,
                        "body": body,
                        "rating": rating,
                        "reviewer": reviewer,
                        "images": images
                    })
                except Exception as e:
                    logger.warning(f"Error processing review: {e}")
                    continue

        return {"reviews_count": len(reviews), "reviews": reviews}

    except Exception as e:
        logger.error(f"Error while fetching reviews: {e}")
        raise HTTPException(status_code=500, detail=f"Error extracting reviews: {str(e)}")

# FastAPI Endpoint
@app.get("/api/reviews", response_model=ReviewResponse)
async def get_reviews(page: str = Query(..., description="URL of the product page to scrape reviews from")):
    """
    Extract reviews from a given product page URL.
    """
    try:
        reviews_data = fetch_reviews(page)
        return reviews_data
    except Exception as e:
        logger.error(f"API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
