# main.py (ASIN抽出ロジック強化・列ずれ修正版)
import gspread
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import random
import os
import json
from collections import defaultdict
import re

# --- 設定項目 ---
SPREADSHEET_KEY = '1NBYKIW94P14fBgTSwlHBwOfuh-S3EYhsLNnALuWFdbQ'

# HTML解析に基づき更新されたCSSセレクタ
SELECTORS = {
    # 4種類の全要素をページ上の出現順に取得するための統合セレクタ
    'all_containers': (
        '[data-component-type="s-search-result"], '
        '[data-component-type="sp-sponsored-brand"], '
        '[data-component-type="sponsored-brands-list"], '
        '[data-component-type="sponsored-brand-video-ad"]'
    ),
    'sponsored_product_label': 'span[data-component-type="s-sponsored-label"]',
}

# --- 関数定義 ---

def extract_asins_from_element(element):
    """
    BeautifulSoupの要素から、複数の方法でASINを抽出する。
    1. 'data-asin'属性を持つ要素を探す
    2. 'href'属性にASINが含まれるリンクを探す
    """
    asins = set()
    # 方法1: 'data-asin'属性から抽出
    for el in element.select('[data-asin]'):
        asin = el.get('data-asin', '').strip()
        if asin and len(asin) == 10:
            asins.add(asin)

    # 方法2: リンクのURLから正規表現で抽出
    for link in element.select('a[href]'):
        href = link.get('href', '')
        match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', href)
        if match:
            asins.add(match.group(1))
            
    return list(asins)

def get_amazon_rankings_for_keyword(page, target_asins_list):
    """
    指定されたキーワードのAmazon検索結果を3ページまで解析し、
    対象ASINの4種類のランキングを計測する。
    """
    results = {
        asin: {
            'organic_rank': '3ページ以内になし',
            'sponsored_product_rank': '3ページ以内になし',
            'sponsored_brand_rank': '3ページ以内になし',
            'sponsored_brand_video_rank': '3ページ以内になし'
        } for asin in target_asins_list
    }
    
    organic_counter = 0
    sponsored_product_counter = 0
    sponsored_brand_counter = 0
    sponsored_brand_video_counter = 0
    
    for i in range(1, 4):
        if i > 1:
            try:
                current_url = page.url
                base_url = current_url.split('&page=')[0].split('?ref=')[0]
                next_page_url = f"{base_url}&page={i}"
                page.goto(next_page_url, wait_until='domcontentloaded', timeout=60000)
            except Exception as e:
                print(f"ページ{i}の読み込みに失敗: {e}")
                break
        
        try:
            # 検索結果が表示されるまで待機
            page.wait_for_selector(SELECTORS['all_containers'], timeout=30000)
        except Exception:
            print(f"{i}ページ目に商品・広告が見つかりませんでした。")
            continue # 次のページ（もしあれば）へ

        print(f"{i}ページ目の解析を開始...")
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        all_elements = soup.select(SELECTORS['all_containers'])
        
        for element in all_elements:
            component_type = element.get('data-component-type', '')
            asins_in_element = extract_asins_from_element(element)

            if not asins_in_element:
                continue

            if component_type == 'sponsored-brand-video-ad':
                sponsored_brand_video_counter += 1
                for asin in asins_in_element:
                    if asin in target_asins_list and results[asin]['sponsored_brand_video_rank'] == '3ページ以内になし':
                        results[asin]['sponsored_brand_video_rank'] = sponsored_brand_video_counter

            elif component_type in ['sp-sponsored-brand', 'sponsored-brands-list']:
                sponsored_brand_counter += 1
                for asin in asins_in_element:
                    if asin in target_asins_list and results[asin]['sponsored_brand_rank'] == '3ページ以内になし':
                        results[asin]['sponsored_brand_rank'] = sponsored_brand_counter

            elif component_type == 's-search-result':
                is_sponsored_product = element.select_one(SELECTORS['sponsored_product_label']) is not None
                if is_sponsored_product:
                    sponsored_product_counter += 1
                    for asin in asins_in_element:
                        if asin in target_asins_list and results[asin]['sponsored_product_rank'] == '3ページ以内になし':
                            results[asin]['sponsored_product_rank'] = sponsored_product_counter
                else:
                    organic_counter += 1
                    for asin in asins_in_element:
                        if asin in target_asins_list and results[asin]['organic_rank'] == '3ページ以内になし':
                            results[asin]['organic_rank'] = organic_counter

        time.sleep(random.uniform(2, 4))
        
    return results

# --- メイン処理 ---
def main():
    gcp_sa_key_str = os.environ.get('GCP_SA_KEY')
    if not gcp_sa_key_str: raise ValueError("環境変数 GCP_SA_KEY が設定されていません。")
    if not SPREADSHEET_KEY or SPREADSHEET_KEY == 'YOUR_SPREADSHEET_KEY': raise ValueError("環境変数 SPREADSHEET_KEY が設定されていません。")

    credentials = json.loads(gcp_sa_key_str)
    gc = gspread.service_account_from_dict(credentials)
    
    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    settings_sheet = spreadsheet.worksheet("設定")
    results_sheet = spreadsheet.worksheet("結果")
    
    search_list = settings_sheet.get_all_records()

    keyword_to_asins = defaultdict(list)
    for row in search_list:
        if row.get('ASIN') and row.get('キーワード'):
            keyword_to_asins[row['キーワード']].append(str(row['ASIN']))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        )
        page = context.new_page()

        for keyword, asins_to_find in keyword_to_asins.items():
            print(f"--- 調査開始: キーワード='{keyword}', 対象ASIN数={len(asins_to_find)} ---")
            
            initial_url = f"https://www.amazon.co.jp/s?k={keyword.replace(' ', '+')}"
            try:
                page.goto(initial_url, wait_until='domcontentloaded', timeout=90000)
                if "見つかりませんでした" in page.content() or "ロボット" in page.content():
                    print(f"キーワード '{keyword}' の検索結果が見つからないか、アクセスがブロックされました。スキップします。")
                    continue
            except Exception as e:
                print(f"初期ページの読み込みに失敗: {e}"); continue
            
            rank_results = get_amazon_rankings_for_keyword(page, asins_to_find)

            for asin, rank_data in rank_results.items():
                new_row = [
                    asin,
                    keyword,
                    rank_data['organic_rank'],
                    rank_data['sponsored_product_rank'],
                    rank_data['sponsored_brand_rank'],
                    rank_data['sponsored_brand_video_rank'],
                    '',  # 「検索結果ページでの商品総数」列用のプレースホルダー
                    datetime.now().strftime('%Y/%m/%d %H:%M')
                ]
                results_sheet.append_row(new_row, value_input_option='USER_ENTERED')
                print(f"結果を書き込みました: {new_row}")
            
            time.sleep(random.uniform(5, 10))

        browser.close()

if __name__ == '__main__':
    main()
