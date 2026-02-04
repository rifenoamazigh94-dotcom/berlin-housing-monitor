#!/usr/bin/env python3
"""
Berlin Housing Monitor
Monitors 6 state-owned housing companies for new apartments
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import hashlib
import re
from datetime import datetime
import os
from typing import List, Dict, Optional

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# User criteria
CRITERIA = {
    'with_wbs': {
        'wbs_amount': 220,
        'rooms_min': 1,
        'rooms_max': 2,
        'size_max': 50,
        'price_max': None  # No limit for WBS apartments
    },
    'without_wbs': {
        'rooms_min': None,  # Any number of rooms
        'rooms_max': None,  # Any number of rooms
        'size_max': None,   # Any size
        'price_max': 700    # Maximum warm rent
    }
}

# Companies to monitor
COMPANIES = {
    'degewo': {
        'name': 'degewo',
        'url': 'https://immosuche.degewo.de/de/search',
        'api_url': 'https://immosuche.degewo.de/de/search/data',
    },
    'gesobau': {
        'name': 'GESOBAU',
        'url': 'https://www.gesobauwohnen.de/wohnungsangebote/',
        'use_immomio': True,
    },
    'gewobag': {
        'name': 'Gewobag',
        'url': 'https://www.gewobag.de/fuer-mieter-und-mietinteressenten/mietangebote/',
    },
    'howoge': {
        'name': 'HOWOGE',
        'url': 'https://www.howoge.de/wohnungen-gewerbe/wohnungssuche.html',
    },
    'stadt_und_land': {
        'name': 'STADT UND LAND',
        'url': 'https://www.stadtundland.de/wohnungen/',
    },
    'wbm': {
        'name': 'WBM',
        'url': 'https://www.wbm.de/wohnungen-berlin/wohnungsangebote/',
    }
}

def send_telegram_message(message: str) -> bool:
    """Send a message via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        response = requests.post(url, data=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False

def load_seen_apartments() -> set:
    """Load previously seen apartment IDs from file"""
    try:
        if os.path.exists('seen_apartments.json'):
            with open('seen_apartments.json', 'r') as f:
                data = json.load(f)
                return set(data.get('apartments', []))
    except Exception as e:
        print(f"Error loading seen apartments: {e}")
    return set()

def save_seen_apartments(apartments: set):
    """Save seen apartment IDs to file"""
    try:
        with open('seen_apartments.json', 'w') as f:
            json.dump({'apartments': list(apartments), 'last_updated': datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"Error saving seen apartments: {e}")

def generate_apartment_id(company: str, apartment_data: dict) -> str:
    """Generate unique ID for an apartment"""
    key = f"{company}_{apartment_data.get('address', '')}_{apartment_data.get('rooms', '')}_{apartment_data.get('size', '')}"
    return hashlib.md5(key.encode()).hexdigest()

def matches_criteria(apartment: dict) -> tuple[bool, str]:
    """
    Check if apartment matches user criteria
    Returns: (matches, reason)
    """
    try:
        rooms = float(apartment.get('rooms', 0))
        size = float(apartment.get('size', 0))
        warm_rent = apartment.get('warm_rent')
        requires_wbs = apartment.get('requires_wbs', False)
        
        # If WBS required - check WBS criteria
        if requires_wbs:
            # Check if size exceeds maximum
            if size > CRITERIA['with_wbs']['size_max']:
                return False, f"Tamaño {size}m² excede el máximo de {CRITERIA['with_wbs']['size_max']}m²"
            
            # Check rooms
            if rooms < CRITERIA['with_wbs']['rooms_min'] or rooms > CRITERIA['with_wbs']['rooms_max']:
                return False, f"Número de habitaciones {rooms} no cumple criterios (1-2)"
            
            return True, "Cumple criterios (CON WBS)"
        
        # If no WBS required, only check price (no room or size restrictions)
        if warm_rent is None:
            return False, "Sin precio especificado"
        
        warm_rent_value = float(warm_rent)
        if warm_rent_value <= CRITERIA['without_wbs']['price_max']:
            return True, f"Cumple criterios (SIN WBS, {warm_rent_value}€ warm, {rooms} hab, {size}m²)"
        else:
            return False, f"Precio {warm_rent_value}€ excede máximo de {CRITERIA['without_wbs']['price_max']}€"
            
    except Exception as e:
        print(f"Error checking criteria: {e}")
        return False, f"Error al verificar criterios: {e}"

def check_degewo() -> List[dict]:
    """Check degewo for new apartments"""
    print("Checking degewo...")
    apartments = []
    
    try:
        # Try API endpoint first
        try:
            response = requests.get(
                'https://immosuche.degewo.de/de/search/data',
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'application/json'
                },
                timeout=15
            )
            
            if response.status_code == 200 and response.text.strip():
                data = response.json()
                
                for item in data.get('immos', []):
                    apartment = {
                        'company': 'degewo',
                        'address': f"{item.get('street', '')} {item.get('houseNumber', '')}, {item.get('district', '')}",
                        'rooms': item.get('rooms'),
                        'size': item.get('area'),
                        'warm_rent': item.get('rentTotal'),
                        'cold_rent': item.get('rentBase'),
                        'requires_wbs': item.get('wbsRequired', False),
                        'url': f"https://immosuche.degewo.de/de/search/details/{item.get('id')}",
                        'available_from': item.get('availableFrom', 'ab sofort')
                    }
                    apartments.append(apartment)
                
                print(f"  → Found {len(apartments)} apartments via API")
                return apartments
        except:
            pass
        
        # Fallback to HTML parsing
        response = requests.get(
            'https://www.degewo.de/immosuche',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find apartment listings
            listings = soup.find_all('a', href=lambda x: x and '/immosuche/details/' in x)
            
            for listing in listings:
                try:
                    # Extract URL
                    url = listing.get('href', '')
                    if not url.startswith('http'):
                        url = f"https://www.degewo.de{url}"
                    
                    # Find apartment details in the listing card
                    parent = listing.find_parent('div')
                    if not parent:
                        continue
                    
                    # Extract title/address
                    title_elem = listing.find('h2') or listing.find('h3')
                    address = title_elem.text.strip() if title_elem else 'N/A'
                    
                    # Extract rooms (look for "X Zimmer" pattern)
                    rooms_text = parent.get_text()
                    rooms = None
                    if 'Zimmer' in rooms_text:
                        rooms_match = re.search(r'(\d+)\s*Zimmer', rooms_text)
                        if rooms_match:
                            rooms = float(rooms_match.group(1))
                    
                    # Extract size (look for "X m²" pattern)
                    size = None
                    if 'm²' in rooms_text:
                        size_match = re.search(r'(\d+[,.]?\d*)\s*m²', rooms_text)
                        if size_match:
                            size = float(size_match.group(1).replace(',', '.'))
                    
                    # Extract warm rent (look for "X €" pattern)
                    warm_rent = None
                    if 'Warmmiete' in rooms_text or '€' in rooms_text:
                        # Look for price patterns
                        price_match = re.search(r'(\d+[,.]?\d*)\s*€', rooms_text)
                        if price_match:
                            warm_rent = float(price_match.group(1).replace(',', '.'))
                    
                    # Check if WBS required
                    requires_wbs = 'WBS' in rooms_text or 'Wohnberechtigungsschein' in rooms_text
                    
                    apartment = {
                        'company': 'degewo',
                        'address': address,
                        'rooms': rooms,
                        'size': size,
                        'warm_rent': warm_rent,
                        'cold_rent': None,
                        'requires_wbs': requires_wbs,
                        'url': url,
                        'available_from': 'ab sofort'
                    }
                    
                    # Only add if we have at least some data
                    if rooms or size or warm_rent:
                        apartments.append(apartment)
                        
                except Exception as e:
                    print(f"  → Error parsing listing: {e}")
                    continue
            
            print(f"  → Found {len(apartments)} apartments via HTML parsing")
                
    except Exception as e:
        print(f"  → Error checking degewo: {e}")
    
    return apartments

def check_generic_company(company_key: str) -> List[dict]:
    """Generic checker for companies using standard websites"""
    print(f"Checking {COMPANIES[company_key]['name']}...")
    apartments = []
    
    try:
        response = requests.get(
            COMPANIES[company_key]['url'],
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=15
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # This is a placeholder - each company needs specific parsing
            # For now, we'll mark it as needing implementation
            print(f"  → Parser for {COMPANIES[company_key]['name']} needs implementation")
            
    except Exception as e:
        print(f"Error checking {COMPANIES[company_key]['name']}: {e}")
    
    return apartments

def format_apartment_message(apartment: dict, reason: str) -> str:
    """Format apartment data as Telegram message"""
    wbs_status = "✅ Con WBS" if apartment.get('requires_wbs') else "❌ Sin WBS"
    
    message = f"""
🏠 <b>Nueva Oferta - {apartment['company']}</b>

📍 <b>Dirección:</b> {apartment.get('address', 'N/A')}
🚪 <b>Habitaciones:</b> {apartment.get('rooms', 'N/A')}
📏 <b>Tamaño:</b> {apartment.get('size', 'N/A')} m²
💰 <b>Alquiler cálido:</b> {apartment.get('warm_rent', 'N/A')} €
💵 <b>Alquiler frío:</b> {apartment.get('cold_rent', 'N/A')} €
📋 <b>WBS:</b> {wbs_status}
📅 <b>Disponible:</b> {apartment.get('available_from', 'N/A')}

✨ <b>Estado:</b> {reason}

🔗 <a href="{apartment.get('url', '#')}">Ver oferta completa</a>
"""
    return message.strip()

def main():
    """Main monitoring function"""
    print(f"\n{'='*60}")
    print(f"Berlin Housing Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    
    # Load previously seen apartments
    seen_apartments = load_seen_apartments()
    new_apartments_found = 0
    
    # Check each company
    all_apartments = []
    
    # Check degewo (has working implementation)
    all_apartments.extend(check_degewo())
    
    # Check other companies (need implementation)
    for company_key in ['gesobau', 'gewobag', 'howoge', 'stadt_und_land', 'wbm']:
        all_apartments.extend(check_generic_company(company_key))
    
    # Process apartments
    for apartment in all_apartments:
        apt_id = generate_apartment_id(apartment['company'], apartment)
        
        # Check if we've seen this apartment before
        if apt_id in seen_apartments:
            continue
        
        # Check if it matches criteria
        matches, reason = matches_criteria(apartment)
        
        if matches:
            print(f"\n✅ New matching apartment found from {apartment['company']}!")
            print(f"   {apartment.get('address', 'N/A')}")
            print(f"   {reason}")
            
            # Send Telegram notification
            message = format_apartment_message(apartment, reason)
            if send_telegram_message(message):
                print(f"   → Telegram notification sent!")
            else:
                print(f"   → Failed to send Telegram notification")
            
            new_apartments_found += 1
        else:
            print(f"   ⏭️  Apartment doesn't match criteria: {reason}")
        
        # Mark as seen
        seen_apartments.add(apt_id)
    
    # Save updated seen apartments
    save_seen_apartments(seen_apartments)
    
    print(f"\n{'='*60}")
    print(f"Scan complete: {new_apartments_found} new matching apartments found")
    print(f"Total apartments checked: {len(all_apartments)}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
