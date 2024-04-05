@echo off
for /D %%G in ("D:\masahiro\*") do (
    for /R %%H in ("%%G\*") do (
        
        if not exist "X:\public\projects\MaNa_20230517_1DSequence\training_data\v1\MN_1099797\%%~nG\%%~nH" (
            xcopy /S /I "%%H" "X:\public\projects\MaNa_20230517_1DSequence\training_data\v1\MN_1099797%%~nG\%%~nH"
        )
    )
)
