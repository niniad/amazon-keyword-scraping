# main.py (全広告対応・効率化・ランキングロジック改修版)
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

# --- 設定項目 ---
# 実行環境の環境変数からスプレッドシートキーを取得するか、直接ここに記述してください
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
    # 各要素のタイプを識別するためのセレクタ
    'sponsored_product_label': 'span[data-component-type="s-sponsored-label"]',
    # 各コンテナからASINを取得するための共通セレクタ
    'asin': '[data-asin]',
}

# --- 関数定義 ---
def get_amazon_rankings_for_keyword(page, target_asins_list):
    """
    指定されたキーワードのAmazon検索結果を3ページまで解析し、
    対象ASINの4種類のランキング（オーガニック、スポンサープロダクト、
    スポンサーブランド、スポンサーブランド動画）を計測する。
    """
    results = {
        asin: {
            'organic_rank': '3ページ以内になし',
            'sponsored_product_rank': '3ページ以内になし',
            'sponsored_brand_rank': '3ページ以内になし',
            'sponsored_brand_video_rank': '3ページ以内になし'
        } for asin in target_asins_list
    }
    
    # ページをまたいでランキングを累積するためのカウンター
    organic_counter = 0
    sponsored_product_counter = 0
    sponsored_brand_counter = 0
    sponsored_brand_video_counter = 0
    
    # 検索結果を3ページまで追跡
    for i in range(1, 4):
        # 2ページ目以降はURLを更新して遷移
        if i > 1:
            try:
                current_url = page.url
                if '&page=' in current_url:
                    base_url = current_url.split('&page=')[0]
                else:
                    base_url = current_url
                next_page_url = f"{base_url}&page={i}"
                page.goto(next_page_url, wait_until='networkidle', timeout=60000)
            except Exception as e:
                print(f"ページ{i}の読み込みに失敗: {e}")
                break

        print(f"{i}ページ目の解析を開始...")
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # --- 新しいランキング計測ロジック ---
        # ページ上の全要素（商品、広告）を出現順に取得
        all_elements = soup.select(SELECTORS['all_containers'])
        
        # 取得した全要素を単一のループで処理し、種類を判定してランキングを計測
        for element in all_elements:
            component_type = element.get('data-component-type', '')

            # 1. スポンサーブランド動画広告の判定
            if component_type == 'sponsored-brand-video-ad':
                sponsored_brand_video_counter += 1
                asins_in_ad = [el.get('data-asin') for el in element.select(SELECTORS['asin']) if el.get('data-asin')]
                for asin in asins_in_ad:
                    if asin in target_asins_list and results[asin]['sponsored_brand_video_rank'] == '3ページ以内になし':
                        results[asin]['sponsored_brand_video_rank'] = sponsored_brand_video_counter

            # 2. スポンサーブランド広告の判定
            elif component_type in ['sp-sponsored-brand', 'sponsored-brands-list']:
                sponsored_brand_counter += 1
                asins_in_ad = [el.get('data-asin') for el in element.select(SELECTORS['asin']) if el.get('data-asin')]
                for asin in asins_in_ad:
                    if asin in target_asins_list and results[asin]['sponsored_brand_rank'] == '3ページ以内になし':
                        results[asin]['sponsored_brand_rank'] = sponsored_brand_counter

            # 3. オーガニック商品とスポンサープロダクト広告の判定
            elif component_type == 's-search-result':
                asin_element = element.select_one(SELECTORS['asin'])
                current_asin = asin_element['data-asin'] if asin_element and 'data-asin' in asin_element.attrs else None
                if not current_asin:
                    continue

                # スポンサーラベルの有無で判定
                is_sponsored_product = element.select_one(SELECTORS['sponsored_product_label']) is not None
                
                if is_sponsored_product:
                    sponsored_product_counter += 1
                    if current_asin in target_asins_list and results[current_asin]['sponsored_product_rank'] == '3ページ以内になし':
                        results[current_asin]['sponsored_product_rank'] = sponsored_product_counter
                else:
                    organic_counter += 1
                    if current_asin in target_asins_list and results[current_asin]['organic_rank'] == '3ページ以内になし':
                        results[current_asin]['organic_rank'] = organic_counter

        # サーバー負荷軽減のための待機
        time.sleep(random.uniform(2, 4))
        
    return results

# --- メイン処理 ---
def main():
    # GitHub ActionsのSecretsから認証情報を読み込む
    gcp_sa_key_str = os.environ.get('GCP_SA_KEY')
    if not gcp_sa_key_str:
        raise ValueError("環境変数 GCP_SA_KEY が設定されていません。")
    if not SPREADSHEET_KEY or SPREADSHEET_KEY == 'YOUR_SPREADSHEET_KEY':
        raise ValueError("環境変数 SPREADSHEET_KEY が設定されていません。")

    try:
        credentials = json.loads(gcp_sa_key_str)
        gc = gspread.service_account_from_dict(credentials)
    except Exception as e:
        print(f"gspreadの認証に失敗しました: {e}")
        return

    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    settings_sheet = spreadsheet.worksheet("設定")
    results_sheet = spreadsheet.worksheet("結果")
    
    search_list = settings_sheet.get_all_records()

    # キーワードごとに対象ASINをまとめる
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
                page.goto(initial_url, wait_until='networkidle', timeout=90000)
                # 検索結果がない、またはロボット判定された場合の対策
                if "見つかりませんでした" in page.content() or "申し訳ありません" in page.content():
                    print(f"キーワード '{keyword}' の検索結果が見つからないか、アクセスがブロックされた可能性があります。スキップします。")
                    continue
            except Exception as e:
                print(f"初期ページの読み込みに失敗: {e}")
                continue
            
            # ランキング取得処理の実行
            rank_results = get_amazon_rankings_for_keyword(page, asins_to_find)

            # 結果をスプレッドシートに書き込み
            for asin, rank_data in rank_results.items():
                new_row = [
                    asin,
                    keyword,
                    rank_data['organic_rank'],
                    rank_data['sponsored_product_rank'],
                    rank_data['sponsored_brand_rank'],
                    rank_data['sponsored_brand_video_rank'],
                    datetime.now().strftime('%Y/%m/%d %H:%M')
                ]
                results_sheet.append_row(new_row, value_input_option='USER_ENTERED')
                print(f"結果を書き込みました: {new_row}")
            
            # 次のキーワードへのアクセス間隔
            time.sleep(random.uniform(5, 10))

        browser.close()

if __name__ == '__main__':
    main()
