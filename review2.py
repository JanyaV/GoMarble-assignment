import os
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from groq import Groq
import json
import time
import logging
import re
import traceback
from typing import List, Optional, Dict

# Initialize FastAPI app
app = FastAPI()

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('review_scraper.log')
    ]
)
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

# Common selectors patterns
REVIEW_SELECTORS = {
    "review_containers": [
        ".review-item",
        "[class*=review]",
        "[class*=Review]",
        ".review-card",
        ".review_container",
        "#reviews-container [class*=review]",
        "[data-review]",
        "[itemtype*=Review]",
        ".product-review"
    ],
    "review_text": [
        ".review-content",
        ".review-text",
        ".review-body",
        "[class*=reviewText]",
        "[class*=review-content]",
        "[class*=ReviewContent]",
        ".review p",
        "[itemprop=reviewBody]",
        "[data-review-text]"
    ],
    "rating": [
        "[class*=rating]",
        "[class*=stars]",
        "[class*=Rating]",
        "[itemprop=ratingValue]",
        "[data-rating]",
        ".rating-score"
    ],
    "reviewer": [
        "[class*=author]",
        "[class*=reviewer]",
        "[itemprop=author]",
        ".review-username",
        ".reviewer-name",
        "[data-reviewer]"
    ]
}

def setup_webdriver():
    """Setup and return configured Chrome WebDriver with detailed error handling."""
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-notifications")
        
        logger.debug("Setting up Chrome WebDriver...")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        logger.debug("WebDriver setup successful")
        return driver
    except Exception as e:
        logger.error(f"WebDriver setup failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise

def find_first_working_selector(soup: BeautifulSoup, selector_list: List[str]) -> Optional[str]:
    """Try multiple selectors and return the first one that finds elements."""
    for selector in selector_list:
        try:
            elements = soup.select(selector)
            if elements:
                logger.debug(f"Found working selector: {selector}")
                return selector
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {str(e)}")
    logger.warning("No working selector found")
    return None

def extract_reviews(soup: BeautifulSoup) -> List[Review]:
    """Extract reviews using multiple selector patterns with detailed logging."""
    reviews = []
    
    # Find review containers
    container_selector = find_first_working_selector(soup, REVIEW_SELECTORS["review_containers"])
    if not container_selector:
        logger.warning("No review containers found")
        return reviews

    review_elements = soup.select(container_selector)
    logger.info(f"Found {len(review_elements)} potential review containers")

    for element in review_elements:
        try:
            # Extract review text
            text_selector = find_first_working_selector(element, REVIEW_SELECTORS["review_text"])
            if not text_selector:
                continue
            
            review_text = element.select_one(text_selector)
            if not review_text or not review_text.get_text(strip=True):
                continue

            # Extract rating
            rating = None
            rating_selector = find_first_working_selector(element, REVIEW_SELECTORS["rating"])
            if rating_selector:
                rating_element = element.select_one(rating_selector)
                if rating_element:
                    rating_text = rating_element.get_text(strip=True)
                    rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                    if rating_match:
                        rating = float(rating_match.group(1))
                        if rating > 5:  # Normalize to 5-star scale
                            rating = rating / 20 if rating <= 100 else 5

            # Extract reviewer name
            reviewer = None
            reviewer_selector = find_first_working_selector(element, REVIEW_SELECTORS["reviewer"])
            if reviewer_selector:
                reviewer_element = element.select_one(reviewer_selector)
                if reviewer_element:
                    reviewer = reviewer_element.get_text(strip=True)

            # Extract images
            images = [
                img["src"] for img in element.select("img[src]")
                if not any(skip in img.get("src", "") for skip in ["avatar", "profile", "user"])
            ]

            review = Review(
                body=review_text.get_text(strip=True),
                rating=rating,
                reviewer=reviewer,
                images=images
            )
            reviews.append(review)
            logger.debug(f"Successfully extracted review: {review.dict()}")

        except Exception as e:
            logger.error(f"Error extracting single review: {str(e)}")
            logger.error(traceback.format_exc())
            continue

    return reviews

async def fetch_reviews(url: str, page_limit: int = 5) -> ReviewResponse:
    """Fetch reviews with comprehensive error handling and logging."""
    driver = None
    try:
        logger.info(f"Starting review extraction for URL: {url}")
        
        driver = setup_webdriver()
        logger.debug("Loading page...")
        driver.get(url)
        
        # Wait for page load
        time.sleep(5)
        logger.debug("Page loaded")
        
        # Get page content
        html_content = driver.page_source
        soup = BeautifulSoup(html_content, 'html.parser')
        logger.debug("HTML parsed successfully")
        
        # Extract reviews
        reviews = extract_reviews(soup)
        
        if not reviews:
            logger.warning("No reviews found on page")
            raise HTTPException(status_code=404, detail="No reviews found on page")
        
        logger.info(f"Successfully extracted {len(reviews)} reviews")
        return ReviewResponse(
            reviews_count=len(reviews),
            reviews=reviews,
            next_page=None  # Pagination handling can be added here
        )

    except WebDriverException as e:
        logger.error(f"WebDriver error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Browser automation error: {str(e)}")
    
    except HTTPException as e:
        raise e
    
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    
    finally:
        if driver:
            logger.debug("Closing WebDriver")
            driver.quit()

@app.get("/api/reviews", response_model=ReviewResponse)
async def get_reviews(
    page: HttpUrl = Query(..., description="URL of the product page to scrape reviews from"),
    page_limit: int = Query(5, description="Maximum number of pages to scrape")
):
    """API endpoint to extract reviews from a given URL."""
    try:
        logger.info(f"Received request for URL: {page}")
        reviews_data = await fetch_reviews(str(page), page_limit)
        return reviews_data
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"API Error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
