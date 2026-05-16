#!/usr/bin/env python3.12
import os
import requests

# === CONFIG ===
SHOPIFY_STORE = "linear-clockworks.myshopify.com"
API_VERSION = "2024-04"
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")

def get_recent_products(limit=50):
    url = f"https://{SHOPIFY_STORE}/admin/api/{API_VERSION}/products.json"
    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}
    params = {
        "limit": limit,
        "order": "created_at desc"
    }
    
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()['products']

def generate_html(products):
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Recent Product Image Gallery</title>
        <style>
            body { font-family: sans-serif; background: #f4f4f4; padding: 20px; }
            .product-row { 
                background: white; 
                margin-bottom: 20px; 
                padding: 15px; 
                border-radius: 8px; 
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }
            .product-name { font-size: 1.2em; font-weight: bold; margin-bottom: 10px; color: #333; }
            .image-container { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 5px; }
            .thumb { 
                width: 150px; 
                height: 150px; 
                object-fit: cover; 
                border: 1px solid #ddd; 
                border-radius: 4px; 
            }
        </style>
    </head>
    <body>
        <h1>Recent Products: Image Gallery</h1>
    """

    for p in products:
        images = p.get('images', [])
        if not images:
            continue
            
        html_content += f'<div class="product-row">'
        html_content += f'<div class="product-name">{p["title"]}</div>'
        html_content += '<div class="image-container">'
        
        # Take up to the first 7 images
        for img in images[:7]:
            src = img.get('src')
            html_content += f'<img src="{src}" class="thumb" alt="Product Image">'
            
        html_content += '</div></div>'

    html_content += "</body></html>"

    with open("product_gallery.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("Success! Open 'product_gallery.html' in your browser.")

if __name__ == "__main__":
    if not ACCESS_TOKEN:
        print("Error: Please set the SHOPIFY_ACCESS_TOKEN environment variable.")
    else:
        products = get_recent_products(50)
        generate_html(products)