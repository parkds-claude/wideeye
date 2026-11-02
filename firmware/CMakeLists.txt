cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wideeye_tab5)

# M5GFX 의 Tab5 패널 프레임버퍼를 2장 → 3장으로 (멱등). 이유·되돌리기는 tools/patch_m5gfx_fbs.py 머리말.
# project() 가 부품을 받은 뒤라 처음 받는 구성에서도 여기서 바로 적용된다(컴파일은 그 뒤다 — 2026-10-12 빈 폴더에서 확인).
# 패치할 파일이 없거나 모양이 바뀌었으면 스크립트가 실패하고 구성이 멈춘다(버퍼 2장짜리를 조용히 만들지 않는다).
execute_process(COMMAND python3 ${CMAKE_CURRENT_LIST_DIR}/tools/patch_m5gfx_fbs.py RESULT_VARIABLE _fb_patch_rc)
if(NOT _fb_patch_rc EQUAL 0)
  message(FATAL_ERROR "patch_m5gfx_fbs.py failed (rc=${_fb_patch_rc})")
endif()
