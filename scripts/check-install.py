#!/usr/bin/env python3
import argparse
from model_assets import check, plan

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='서버를 시작하지 않는 필수 설치 검사')
    parser.add_argument('--packages-only', action='store_true')
    parser.add_argument('--plan', action='store_true', help='파일 존재 여부만 읽어 모델 설치 계획 표시')
    args = parser.parse_args()
    if args.plan:
        plan()
        raise SystemExit(0)
    check(args.packages_only)
    print('Python 패키지 확인 완료' if args.packages_only else '필수 실행 파일·패키지·모델 확인 완료')
