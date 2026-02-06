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
        print(f"ERROR: Telegram credentials not configured")
        print(f"  TELEGRAM_BOT_TOKEN present: {bool(TELEGRAM_BOT_TOKEN)}")
        print(f"  TELEGRAM_CHAT_ID present: {bool(TELEGRAM_CHAT_ID)}")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        }
        
        print(f"  → Sending to Telegram API...")
        print(f"  → Chat ID: {TELEGRAM_CHAT_ID}")
        print(f"  → Bot Token: {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-5:]}")
        
        response = requests.post(url, data=data, timeout=10)
        
        print(f"  → Response status: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  → SUCCESS: Message sent!")
            return True
        else:
            print(f"  → FAILED: {response.text}")
            return False
            
    except Exception as e:
        print(f"  → ERROR sending Telegram message: {e}")
        import traceback
        traceback.print_exc()
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

def check_inberlinwohnen() -> List[dict]:
    """Check inberlinwohnen.de - centralized portal for all 6 companies"""
    print("Checking inberlinwohnen.de (portal centralizado de las 6 empresas)...")
    apartments = []
    
    try:
        response = requests.get(
            'https://www.inberlinwohnen.de/wohnungsfinder/',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find apartment listings
            # The site uses a specific structure for apartment cards
            listings = soup.find_all('div', class_=lambda x: x and 'apartment' in x.lower() if x else False)
            
            if not listings:
                # Try alternative selectors
                listings = soup.find_all('article') or soup.find_all('div', class_='offer')
            
            print(f"  → Found {len(listings)} potential listings")
            
            for listing in listings:
                try:
                    # Extract company name
                    company_elem = listing.find(text=re.compile(r'(degewo|GESOBAU|Gewobag|HOWOGE|STADT UND LAND|WBM)', re.I))
                    company = 'unknown'
                    if company_elem:
                        company_text = str(company_elem).lower()
                        if 'degewo' in company_text:
                            company = 'degewo'
                        elif 'gesobau' in company_text:
                            company = 'GESOBAU'
                        elif 'gewobag' in company_text:
                            company = 'Gewobag'
                        elif 'howoge' in company_text:
                            company = 'HOWOGE'
                        elif 'stadt' in company_text:
                            company = 'STADT UND LAND'
                        elif 'wbm' in company_text:
                            company = 'WBM'
                    
                    # Extract URL
                    link = listing.find('a', href=True)
                    url = link['href'] if link else ''
                    if url and not url.startswith('http'):
                        url = f"https://www.inberlinwohnen.de{url}"
                    
                    # Extract text content
                    text = listing.get_text()
                    
                    # Extract address
                    address = 'N/A'
                    address_match = re.search(r'([A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜa-zäöüß]+)*(?:straße|str\.|platz|weg|allee))\s*\d*', text, re.I)
                    if address_match:
                        address = address_match.group(0)
                    
                    # Extract rooms
                    rooms = None
                    rooms_match = re.search(r'(\d+(?:[,\.]\d+)?)\s*(?:Zimmer|Zi\.)', text)
                    if rooms_match:
                        rooms = float(rooms_match.group(1).replace(',', '.'))
                    
                    # Extract size
                    size = None
                    size_match = re.search(r'(\d+(?:[,\.]\d+)?)\s*m[²2]', text)
                    if size_match:
                        size = float(size_match.group(1).replace(',', '.'))
                    
                    # Extract warm rent
                    warm_rent = None
                    # Look for patterns like "600,00 €" or "600 EUR"
                    price_match = re.search(r'(\d{1,4}[,\.]?\d{0,2})\s*(?:€|EUR|Euro)', text)
                    if price_match:
                        warm_rent = float(price_match.group(1).replace(',', '.').replace('.', '', text.count('.') - 1))
                    
                    # Check WBS requirement
                    requires_wbs = bool(re.search(r'WBS|Wohnberechtigungsschein', text, re.I))
                    
                    apartment = {
                        'company': company,
                        'address': address,
                        'rooms': rooms,
                        'size': size,
                        'warm_rent': warm_rent,
                        'cold_rent': None,
                        'requires_wbs': requires_wbs,
                        'url': url,
                        'available_from': 'ab sofort'
                    }
                    
                    # Only add if we extracted some meaningful data
                    if url and (rooms or size or warm_rent):
                        apartments.append(apartment)
                        
                except Exception as e:
                    print(f"  → Error parsing listing: {e}")
                    continue
            
            print(f"  → Extracted {len(apartments)} apartments with data")
                
    except Exception as e:
        print(f"  → Error checking inberlinwohnen: {e}")
    
    return apartments

def fetch_degewo_apartment_details(url: str) -> dict:
    """Fetch detailed information from individual degewo apartment page"""
    try:
        response = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        if response.status_code != 200:
            return {}
        
        soup = BeautifulSoup(response.text, 'html.parser')
        details = {}
        
        # Extract from Objektdetails section
        objektdetails = soup.find('h2', string='Objektdetails')
        if objektdetails:
            table = objektdetails.find_next('table')
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) == 2:
                        key = cols[0].get_text().strip()
                        value = cols[1].get_text().strip()
                        
                        if 'Zimmer' in key:
                            try:
                                details['rooms'] = float(value.replace(',', '.'))
                            except:
                                pass
                        elif 'Wohnfläche' in key:
                            try:
                                size_match = re.search(r'(\d+[,\.]?\d*)', value)
                                if size_match:
                                    details['size'] = float(size_match.group(1).replace(',', '.'))
                            except:
                                pass
                        elif 'WBS benötigt' in key:
                            details['requires_wbs'] = 'Ja' in value or 'ja' in value
                        elif 'Verfügbar ab' in key:
                            details['available_from'] = value
        
        # Extract address
        address_elem = soup.find('h1')
        if address_elem:
            # Address is usually in a separate element near the h1
            address_container = soup.find(string=re.compile(r'\d{5}\s+Berlin'))
            if address_container:
                # Get the street address that appears before the postal code
                street_elem = address_container.find_parent().find_previous('div')
                if street_elem:
                    street = street_elem.get_text().strip()
                    postal_match = re.search(r'(\d{5}\s+Berlin)', address_container)
                    if postal_match:
                        details['address'] = f"{street}, {postal_match.group(1)}"
        
        # If address not found, try alternative method
        if 'address' not in details:
            # Look for address near map section
            addr_parts = []
            for text in soup.stripped_strings:
                if re.match(r'^[A-ZÄÖÜ][\wäöüß\-]+(?:straße|str\.|platz|weg|allee)', text, re.I):
                    addr_parts.append(text)
                    break
            for text in soup.stripped_strings:
                if re.match(r'^\d{5}\s+Berlin', text):
                    addr_parts.append(text)
                    break
            if addr_parts:
                details['address'] = ', '.join(addr_parts)
        
        # Extract prices from the cost section at top
        # Look for "Nettokaltmiete" and "Gesamt"
        nettokalt_elem = soup.find(string=re.compile(r'Nettokaltmiete', re.I))
        if nettokalt_elem:
            price_container = nettokalt_elem.find_parent()
            if price_container:
                price_text = price_container.get_text()
                cold_match = re.search(r'(\d+[,\.]\d+)\s*€', price_text)
                if cold_match:
                    details['cold_rent'] = float(cold_match.group(1).replace(',', '.').replace('.', '', price_text.count('.') - 1))
        
        gesamt_elem = soup.find(string=re.compile(r'Gesamt\s*$', re.I))
        if not gesamt_elem:
            # Try finding it in a different way
            for elem in soup.find_all(['div', 'span', 'p']):
                text = elem.get_text().strip()
                if text == 'Gesamt' or 'Gesamt' in text:
                    # Look for price near this element
                    price_text = elem.find_next().get_text() if elem.find_next() else ''
                    warm_match = re.search(r'(\d+[,\.]\d+)\s*€', price_text)
                    if warm_match:
                        details['warm_rent'] = float(warm_match.group(1).replace(',', '.').replace('.', '', price_text.count('.') - 1))
                        break
        
        return details
        
    except Exception as e:
        print(f"    → Error fetching details: {e}")
        return {}

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
                    
                    # Fetch detailed information from the individual apartment page
                    print(f"    → Fetching details from {url[:60]}...")
                    detailed_info = fetch_degewo_apartment_details(url)
                    
                    # Extract title from listing (as fallback for address)
                    title_elem = listing.find('h2') or listing.find('h3')
                    title = title_elem.text.strip() if title_elem else 'N/A'
                    
                    apartment = {
                        'company': 'degewo',
                        'address': detailed_info.get('address', title),
                        'rooms': detailed_info.get('rooms'),
                        'size': detailed_info.get('size'),
                        'warm_rent': detailed_info.get('warm_rent'),
                        'cold_rent': detailed_info.get('cold_rent'),
                        'requires_wbs': detailed_info.get('requires_wbs', False),
                        'url': url,
                        'available_from': detailed_info.get('available_from', 'ab sofort')
                    }
                    
                    # Only add if we extracted meaningful data
                    if apartment['rooms'] or apartment['size'] or apartment['warm_rent']:
                        apartments.append(apartment)
                        print(f"    → ✓ Extracted: {apartment['rooms']} Zi, {apartment['size']}m², {apartment['warm_rent']}€")
                    else:
                        print(f"    → ✗ Could not extract complete data")
                        
                except Exception as e:
                    print(f"  → Error parsing listing: {e}")
                    continue
            
            print(f"  → Found {len(apartments)} apartments via HTML parsing")
                
    except Exception as e:
        print(f"  → Error checking degewo: {e}")
    
    return apartments

def check_howoge() -> List[dict]:
    """Check HOWOGE for new apartments"""
    print("Checking HOWOGE...")
    apartments = []
    
    try:
        # HOWOGE uses JavaScript, so we'll try to detect any apartment listings
        response = requests.get(
            'https://www.howoge.de/immobiliensuche/wohnungssuche.html',
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=15
        )
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for apartment detail links
            links = soup.find_all('a', href=lambda x: x and '/wohnungssuche/detail/' in x if x else False)
            
            print(f"  → Found {len(links)} apartment links")
            
            for link in links:
                try:
                    url = link.get('href', '')
                    if not url.startswith('http'):
                        url = f"https://www.howoge.de{url}"
                    
                    # Extract apartment ID from URL
                    apt_id_match = re.search(r'/detail/(\d+-\d+-\d+)', url)
                    if not apt_id_match:
                        continue
                    
                    # Get the parent container
                    parent = link.find_parent(['div', 'article', 'li'])
                    if not parent:
                        parent = link
                    
                    text = parent.get_text()
                    
                    # Extract address
                    address = link.get_text().strip() if link.get_text() else 'N/A'
                    
                    # Extract rooms
                    rooms = None
                    rooms_match = re.search(r'(\d+(?:[,\.]\d+)?)\s*(?:Zimmer|Zi\.)', text)
                    if rooms_match:
                        rooms = float(rooms_match.group(1).replace(',', '.'))
                    
                    # Extract size
                    size = None
                    size_match = re.search(r'(\d+(?:[,\.]\d+)?)\s*m[²2]', text)
                    if size_match:
                        size = float(size_match.group(1).replace(',', '.'))
                    
                    # Extract warm rent
                    warm_rent = None
                    price_match = re.search(r'(\d{1,4}[,\.]?\d{0,2})\s*(?:€|EUR)', text)
                    if price_match:
                        warm_rent = float(price_match.group(1).replace(',', '.').replace('.', '', text.count('.') - 1))
                    
                    # Check WBS
                    requires_wbs = bool(re.search(r'WBS|Wohnberechtigungsschein', text, re.I))
                    
                    apartment = {
                        'company': 'HOWOGE',
                        'address': address,
                        'rooms': rooms,
                        'size': size,
                        'warm_rent': warm_rent,
                        'cold_rent': None,
                        'requires_wbs': requires_wbs,
                        'url': url,
                        'available_from': 'ab sofort'
                    }
                    
                    apartments.append(apartment)
                        
                except Exception as e:
                    print(f"  → Error parsing HOWOGE listing: {e}")
                    continue
            
            print(f"  → Extracted {len(apartments)} apartments")
            
            # If no apartments found via HTML, create a generic notification
            if len(apartments) == 0 and len(links) == 0:
                print("  → HOWOGE usa JavaScript - no se pueden extraer detalles")
                print("  → Revisa manualmente: https://www.howoge.de/immobiliensuche/wohnungssuche.html")
                
    except Exception as e:
        print(f"  → Error checking HOWOGE: {e}")
    
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
    
    # Verify credentials
    print("Checking Telegram credentials...")
    print(f"  TELEGRAM_BOT_TOKEN: {'✅ Set' if TELEGRAM_BOT_TOKEN else '❌ NOT SET'}")
    print(f"  TELEGRAM_CHAT_ID: {'✅ Set' if TELEGRAM_CHAT_ID else '❌ NOT SET'}")
    if TELEGRAM_BOT_TOKEN:
        print(f"  Token starts with: {TELEGRAM_BOT_TOKEN[:15]}...")
    if TELEGRAM_CHAT_ID:
        print(f"  Chat ID: {TELEGRAM_CHAT_ID}")
    print()
    
    # Load previously seen apartments
    seen_apartments = load_seen_apartments()
    new_apartments_found = 0
    
    # Check each company
    all_apartments = []
    
    # OPTION 1: Check centralized portal (all 6 companies at once)
    print("="*60)
    print("Revisando portal centralizado (todas las empresas)...")
    print("="*60)
    all_apartments.extend(check_inberlinwohnen())
    
    # OPTION 2: Check degewo directly
    print("\n" + "="*60)
    print("Revisando degewo directamente...")
    print("="*60)
    all_apartments.extend(check_degewo())
    
    # OPTION 3: Check HOWOGE directly
    print("\n" + "="*60)
    print("Revisando HOWOGE directamente...")
    print("="*60)
    all_apartments.extend(check_howoge())
    
    # Note: Other companies (GESOBAU, Gewobag, STADT UND LAND, WBM) use complex systems
    # They are included in inberlinwohnen or require more advanced scraping
    
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
