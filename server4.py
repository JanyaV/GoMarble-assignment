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

app = FastAPI()

# Groq API Configuration
GROQ_API_KEY = "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"  # Replace with your API key
client = Groq(api_key=GROQ_API_KEY)

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger()

# Response model for API
class ReviewResponse(BaseModel):
    reviews_count: int
    reviews: list[dict]

# Fetch reviews with optional Groq API refinement
def fetch_reviews(url: str):
    try:
        # Selenium setup
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.get(url)
        time.sleep(5)  # Wait for the page to load
        html_content = driver.page_source
        driver.quit()

        # Parse the page with BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        reviews = []

        # Predefined selectors based on the provided HTML structure
        predefined_selectors = {
            "review": ".Review__header",  # Identify each review block
            "reviewer": ".Review__author",  # The author's name
            "rating": ".Rating__stars",  # The star ratings container
            "body": ".Review__body",  # The body/content of the review
            "images": ".Review__photos img",  # Image links inside review
        }

        # Extract reviews using predefined selectors
        for review_block in soup.select(predefined_selectors["review"]):
            try:
                # Extract review body text
                body = review_block.select_one(predefined_selectors["body"]).get_text(strip=True)

                # Extract reviewer name
                reviewer = review_block.select_one(predefined_selectors["reviewer"]).get_text(strip=True)

                # Calculate rating based on the filled stars
                rating = len(review_block.select(".Rating__stars .stars__icon--100"))

                # Extract images, if any
                images = [img["src"] for img in review_block.select(predefined_selectors["images"]) if img.get("src")]

                reviews.append({
                    "reviewer": reviewer,
                    "rating": rating,
                    "body": body,
                    "images": images,
                })
            except Exception as e:
                logger.warning(f"Error processing review block: {e}")
                continue

        # If reviews are not found, fall back to Groq API to refine selectors
        if not reviews:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are an assistant with expertise in parsing HTML. The HTML structure you will analyze has reviews with the following elements: review body, reviewer name, rating (star icons), and images. Please extract CSS selectors for each of these elements."},
                    {"role": "user", "content": f"Here is an HTML snippet for a review page: {html_content[:3000]}"}
                ],
                stream=False,
                max_tokens=1024
            )

            logger.info(f"Groq API Response: {response}")
            try:
                # Try parsing Groq's response as JSON
                selectors = json.loads(response.choices[0].message.content)
                logger.info(f"Groq selectors: {selectors}")

                # Use Groq selectors to extract reviews
                for review in soup.select(selectors["review"]):
                    try:
                        body = review.select_one(selectors["body"]).get_text(strip=True)
                        reviewer = review.select_one(selectors["reviewer"]).get_text(strip=True)
                        rating = len(review.select(selectors["rating"] + " .stars__icon--100"))
                        images = [
                            img["src"]
                            for img in review.select(selectors["images"])
                            if img.get("src")
                        ]

                        reviews.append({
                            "reviewer": reviewer,
                            "rating": rating,
                            "body": body,
                            "images": images,
                        })
                    except Exception as e:
                        logger.warning(f"Error processing review with Groq selectors: {e}")
                        continue
            except json.JSONDecodeError:
                # If Groq response is not in JSON format, handle the error
                logger.error("Failed to decode Groq API response as JSON.")
                logger.info(f"Groq Response Content: {response.choices[0].message.content}")
                raise HTTPException(status_code=500, detail="Error parsing Groq API response.")

        return {"reviews_count": len(reviews), "reviews": reviews}

    except Exception as e:
        logger.error(f"Error while fetching reviews: {e}")
        raise HTTPException(status_code=500, detail=f"Error extracting reviews: {str(e)}")

@app.get("/api/reviews", response_model=ReviewResponse)
async def get_reviews(page: str = Query(..., description="URL of the product page to scrape reviews from")):
    try:
        reviews_data = fetch_reviews(page)
        return reviews_data
    except Exception as e:
        logger.error(f"API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
