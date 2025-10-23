# main.py
import gspread
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import random
import os
import json

# --- 設定項目 ---
# 1. Googleスプレッドシートのキー (URLの .../d/【この部分】/edit...)
SPREADSHEET_KEY = 'YOUR_SPREADSHEET_KEY'

# 2. 各要素を特定するためのセレクタ
SELECTORS = {
    'item_container': '[data-component-type="s-search-result"]',
    'sponsored_product_label': 'span[data-component-type="s-sponsored-label"]',
    'asin': '[data-asin]',
}

# --- 関数定義 ---

def get_amazon_ranking(page, target_asin):
    """ブラウザでページを開き、HTMLを解析して順位を返す"""
    result = {'organic_rank': '3ページ以内になし', 'sponsored_product_rank': '3ページ以内になし'}
    organic_counter = 0
    sponsored_counter = 0

    for i in range(1, 4):
        search_url = f"{page.url}&page={i}"
        if i > 1:
            page.goto(search_url, wait_until='networkidle', timeout=60000)
        
        print(f"{i}ページ目の解析を開始...")
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        items = soup.select(SELECTORS['item_container'])
        if not items:
            print("商品リストが見つかりません。ページの構造が変わった可能性があります。")
            break
            
        for item in items:
            asin_elem = item.select_one(SELECTORS['asin'])
            current_asin = asin_elem['data-asin'] if asin_elem and 'data-asin' in asin_elem.attrs else None
            if not current_asin: continue

            is_sponsored = item.select_one(SELECTORS['sponsored_product_label']) is not None
            
            if is_sponsored:
                sponsored_counter += 1
                if current_asin == target_asin and result['sponsored_product_rank'] == '3ページ以内になし':
                    result['sponsored_product_rank'] = sponsored_counter
            else:
                organic_counter += 1
                if current_asin == target_asin and result['organic_rank'] == '3ページ以内になし':
                    result['organic_rank'] = organic_counter
        
        time.sleep(random.uniform(2, 5)) # ページ遷移の間にランダムな待機
        
    return result

# --- メイン処理 ---
def main():
    # サービスアカウントキーを環境変数から読み込む
    gcp_sa_key_str = os.environ.get('GCP_SA_KEY')
    if not gcp_sa_key_str:
        raise ValueError("環境変数 GCP_SA_KEY が設定されていません。")
    
    credentials = json.loads(gcp_sa_key_str)
    gc = gspread.service_account_from_dict(credentials)
    
    # スプレッドシートを開く
    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    settings_sheet = spreadsheet.worksheet("設定")
    results_sheet = spreadsheet.worksheet("結果")
    
    # 調査リストを取得 (ヘッダーを除く)
    search_list = settings_sheet.get_all_records()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) # headless=Falseにするとブラウザの動きが見える
        page = browser.new_page()

        for item in search_list:
            asin = item.get('ASIN')
            keyword = item.get('キーワード')
            if not asin or not keyword: continue

            print(f"--- 調査開始: ASIN={asin}, キーワード={keyword} ---")
            initial_url = f"https://www.amazon.co.jp/s?k={keyword}"
            page.goto(initial_url, wait_until='networkidle', timeout=60000)
            
            rank_data = get_amazon_ranking(page, str(asin))

            # 結果をスプレッドシートに書き込み
            new_row = [
                str(asin), keyword,
                rank_data['organic_rank'],
                rank_data['sponsored_product_rank'],
                datetime.now().strftime('%Y/%m/%d %H:%M')
            ]
            results_sheet.append_row(new_row)
            print(f"結果を書き込みました: {new_row}")
            time.sleep(random.uniform(5, 10)) # 次のキーワードへ行く前に長めに待機

        browser.close()

if __name__ == '__main__':
    main()
