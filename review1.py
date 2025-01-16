import os
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from groq import Groq
import json
import time
import logging
import re
from typing import List, Optional, Dict
from urllib.parse import urljoin

# Initialize FastAPI app
app = FastAPI()

# Groq API Configuration

GROQ_API_KEY = "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"

client = Groq(api_key=GROQ_API_KEY)

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger()

# Response models
class Review(BaseModel):
    body: str
    rating: Optional[float] = None
    reviewer: Optional[str] = None
    images: List[str] = []

class ReviewResponse(BaseModel):
    reviews_count: int
    reviews: List[Review]
    next_page: Optional[str] = None

# Default selectors as fallback
DEFAULT_SELECTORS = {
    "review_item": [
        ".review",
        "[class*=review]",
        "[class*=Review]",
        ".product-review",
        ".comment",
        "[data-review]"
    ],
    "body": [
        ".review-content",
        ".review-text",
        ".review-body",
        "p",
        "[class*=review-content]",
        "[class*=ReviewText]"
    ],
    "rating": [
        ".rating",
        ".stars",
        "[class*=rating]",
        "[class*=stars]",
        "[data-rating]"
    ],
    "reviewer": [
        ".reviewer-name",
        ".author",
        ".username",
        "[class*=author]",
        "[class*=reviewer]"
    ]
}

def extract_json_from_llm_response(text: str) -> Dict:
    """Extract JSON from LLM response even if it's wrapped in text."""
    try:
        # Try to find JSON-like structure in the text
        json_match = re.search(r'\{[^{}]*\}', text)
        if json_match:
            return json.loads(json_match.group(0))
        return {}
    except Exception as e:
        logger.error(f"Error extracting JSON from LLM response: {e}")
        return {}

def get_llm_selectors(html_content: str) -> dict:
    """Get CSS selectors using LLM with improved error handling."""
    try:
        prompt = """
        Analyze this HTML and return ONLY a JSON object with CSS selectors for reviews.
        Format:
        {
            "review_item": "CSS selector for individual review container",
            "body": "CSS selector for review text",
            "rating": "CSS selector for rating",
            "reviewer": "CSS selector for reviewer name"
        }
        """
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"HTML: {html_content[:3000]}"}
            ],
            stream=False,
            max_tokens=1024
        )
        
        # Try to extract JSON from response
        selectors = extract_json_from_llm_response(response.choices[0].message.content)
        
        if not selectors:
            logger.warning("Using default selectors as fallback")
            return DEFAULT_SELECTORS
            
        return selectors
    except Exception as e:
        logger.error(f"Error getting LLM selectors: {e}")
        return DEFAULT_SELECTORS

def find_working_selector(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
    """Try multiple selectors and return the first one that works."""
    for selector in selectors:
        try:
            elements = soup.select(selector)
            if elements:
                return selector
        except Exception:
            continue
    return None

def extract_review_data(review_element: BeautifulSoup, selectors: dict) -> Optional[Review]:
    """Extract data for a single review with improved error handling."""
    try:
        # Get review body
        body_selector = find_working_selector(review_element, selectors["body"])
        if not body_selector:
            return None
            
        body_element = review_element.select_one(body_selector)
        if not body_element:
            return None
            
        body = body_element.get_text(strip=True)
        if not body:
            return None

        # Get rating (optional)
        rating = None
        rating_selector = find_working_selector(review_element, selectors["rating"])
        if rating_selector:
            rating_element = review_element.select_one(rating_selector)
            if rating_element:
                rating_text = rating_element.get_text(strip=True)
                # Try to extract number from text
                rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                if rating_match:
                    rating = float(rating_match.group(1))

        # Get reviewer name (optional)
        reviewer = None
        reviewer_selector = find_working_selector(review_element, selectors["reviewer"])
        if reviewer_selector:
            reviewer_element = review_element.select_one(reviewer_selector)
            if reviewer_element:
                reviewer = reviewer_element.get_text(strip=True)

        # Get images
        images = [img["src"] for img in review_element.select("img[src]")]

        return Review(
            body=body,
            rating=rating,
            reviewer=reviewer,
            images=images
        )
    except Exception as e:
        logger.error(f"Error extracting review data: {e}")
        return None

async def fetch_reviews(url: str, page_limit: int = 5) -> ReviewResponse:
    """Fetch reviews with improved error handling and fallback mechanisms."""
    all_reviews = []
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.get(url)
        time.sleep(5)  # Wait for page load
        
        # Get HTML and selectors
        html_content = driver.page_source
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Get selectors from LLM or use defaults
        selectors = get_llm_selectors(html_content)
        
        # Find working review item selector
        review_item_selector = find_working_selector(soup, selectors["review_item"])
        if not review_item_selector:
            raise HTTPException(status_code=404, detail="No reviews found on page")
            
        # Extract reviews
        for review_element in soup.select(review_item_selector):
            review_data = extract_review_data(review_element, selectors)
            if review_data:
                all_reviews.append(review_data)
        
        if not all_reviews:
            raise HTTPException(status_code=404, detail="No valid reviews found on page")
            
        return ReviewResponse(
            reviews_count=len(all_reviews),
            reviews=all_reviews
        )
            
    except Exception as e:
        logger.error(f"Error in fetch_reviews: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'driver' in locals():
            driver.quit()

@app.get("/api/reviews", response_model=ReviewResponse)
async def get_reviews(
    page: HttpUrl = Query(..., description="URL of the product page to scrape reviews from"),
    page_limit: int = Query(5, description="Maximum number of pages to scrape")
):
    """Extract reviews from a given product page URL."""
    try:
        reviews_data = await fetch_reviews(str(page), page_limit)
        return reviews_data
    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
