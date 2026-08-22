"""Behavioral conformance tests for the durable execution read contract."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_cli.execution_contract import (
    ACTION_CONTRACT_VERSION,
    CONTRACT_VERSION,
    ContractConflictError,
    ContractCursorGoneError,
    ContractDataError,
    ContractForbiddenError,
    ContractNotFoundError,
    ContractRateLimitedError,
    ContractValidationError,
    ExecutionContractStore,
    UnsupportedContractVersionError,
    action_contract_schema,
    action_contract_schema_artifact,
    canonical_digest,
    contract_capabilities,
    contract_schema,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "execution_contract"
PRIOR_V1_BASE_SHA = "28f24e44b890b817ca23a31e678e0e34f1781d1e"
PRIOR_V1_SOURCE_SHA256 = (
    "6fed747bb9028e4f379d9444aa7fce15b0255a7622b2befe3feb214f748b3018"
)
PRIOR_V1_SOURCE_B64 = (
    "eNrtfXt/28ax6P/8FAjOvT+TCcUjOY/TsmFyVIl2dKqHjySnSV1fBCJBCTUFsABoWXH13e/Ovp/AgqRk2bV/iS0Bi93Z2dnZee1M"
    "GIb7yyK+mCf9YFHks3SebJWTfJFMg5+S4jopg+RdMllWaZ4FRRJPg0meVUU8qQadzvlVIr2dJ9PLpAjSMqjQ83hZXeVFWsVV+jYJ"
    "5vkknqPvJ3kxDWZ5gT6rkiKL5/PbTn5RJsVbgEDuLJ0lk9sJQDVNJmmJnpX9AH2dFAiy5G2SVej3OJsGqJ/rFPXUSWbokwoGSdJF"
    "VQ6C4KBCH8/Ti6SIq2R+G0xzNJ0sr4I0myFI6QflcjJJyjKYFfl1MLmKq06FoOsHJXoIoFylZZUXtxg90HCLTmaZBSWaXgJgBX+J"
    "s4sYPVheX8dFmpQIO7vzebBYXszTCUZcGSzLJDj738O0SoLffrvOp8moyH/7DU/it99enO4+P9oN/rlMitsoz+a3v/2GZnA2uUqu"
    "484EdQBo6QfX6WVBfwRsvk0AMOiBjIRfBXEBqFyg39MquClgQLSg5DsE2TnFGAM/rUoVs6THOLtFv6bTJJskWxfx5A16TZGLR4CO"
    "qyTrpFmQZzClP4+fHxwHB0dH4/2D3fMxmhoilKxEtILG/ROmCrJwQYVXm1BKkSB6SOCnjtQ8niszypfVRf4OUw50gwlx98XBoBOG"
    "YaeDVy6KZstqWSRRFKTXi7xAQGZorcmkOx367CourxBFsF/JP+jBoEjKfFmg9WWv/lHmGfs550+LhP1UJmhVKv6i/Occ4flr/isa"
    "mP1cpdf8q+UynRJ4YR8hOkNjM3jpk+s4i9FGQgS4XCwQWCVpP42reDKPS0SW7AP+iLdIYCzpNf69jyFAO6GKyY+/o/Uinyzi6kqC"
    "4AX6lbyobhdpdsme72aIyg4qIKEcAXYUL+BtPzhZkLXqB5O4rOhCXGG2EU3m6YAgJSrjWRLhNRMzzdDWi4CRILJyfYdYwZx9gsk4"
    "qt5lamO0tlUMJEWbXSZVRN9d5Qjtnb2T4/PT3b3z6Ofx6dnByXEwCkLSYMC5zQBgG7zdCTuIpuSG8Ih3cLb30/hoN3p2cDg+3j0a"
    "o/ez8L3e/d2gxFt2AORjfrx/8Hx8dh79NN7dH5/CCITHbo0ZKFt7lL1uka2/tZ9eJmXl7Gn38PnJ6cH5T0fQWXkVP/32u7Bzdn5y"
    "OmYNxXR2OuOfx8fn0en4HP2DnkVnY9Tt/hl69/V28GXw9Bv013fb+K/O/vjZ7svD8+jF7nPU18HfYMLfbneOdn9RHj3d3u68OD0B"
    "rES7x3s/nZwqI2qvJOSFAv9b7EzZYidQipd1klA0Rlo3hyd7f2nfF+Lbb8y+YEJ//vV8DFj4ZvuP3xkN/rp7gPB8fj4+enF+Rmds"
    "bSOwuT3Y3ulEf0ULM47Qs+PxHsb66a9Sm28H2/YmB8cH5we7h17dAfByu51OZ/zLeO8lWd3zXTIttGN+T7IyqbqdAP15j/+GP2GM"
    "jr5FlUzDvniGjqCl+gQddRna7/Kj+CZOK/QsYqez/HIC6J7PMetF+x51WGpjsEM7wocv4k32t7MYLaD9FR3D8XYRF1Uaz63v4uuL"
    "9HKZL0v69q7T65yPT48OjhHSW2Hvsc1if7x3cOaA/X24SLIpXkW0oOiwm7+FUYMQCQppQX5EB05SlHged73O6XhvfPDiPDp5eb53"
    "cmRFRShNPAjZPDkBkF/4LBDVcKABXMKNzn990YBmwaexHKQiS7zE0kMKP7paUIlJfc0IeGAlVOktQ5nlJceh5Z2EVBkoLHsOmHwV"
    "EdFY23ZE2BpgQai84i8Bd2xxnFQaItknQuc0iFBI2MILQQggYoPCs2WG2CQSyvB7Pg6M0EFny+HJX8f7ETp3js8OYFOcDYNpOqle"
    "lRWSAviI8Ovr1wgGsmaCqQx1qOCPscO2ELD8kzvcrkcmyljRCv30+cdqj4yVWbsUNNfI4/z4HPz50slbRDsVRnNcO7SCL7tBqRle"
    "HdTRgQ+earlZAytsYnj1TM/EnaVpqynYOHmrKTTjwJykYwoCFnkKRjMKUG0bAVVtMwaa1ugOMYODfSRyoD1eJINJfr1AQ3aL8P91"
    "EXP9FyLTfxWTxb+St1Uv6r7a3vpjvDV7/X7n6Z3869fo1/8T9jrR+OeD/fHx3tjRZfJ26tHJ2U+7SNS1fc/bfvfNHW7KRLWDY0T3"
    "MK7PR1jcPjm0tH3193fb21t/f7cz+/u7/5q9Ro07HayEBVyIZzL8uCjyonu6zEDpwr/0hgTjYfjnuEwC8h1otZN5js4ILrwGsKhI"
    "mS0HWMGlA7B+f47n6RTvVDKCfWAx1l6Mlr4I0myxrIQRpEQ9lLNbrFBTOwW37dhGPc6rZ/kym/qNuYs6K9AJVs1vA2pP4pPLL/4B"
    "hhcOSfIuLe1jPsuLi3SKTivPQbMAjrYqnaVovhfJPM8uy6DKgxidW+jELdA7ZuXiFqpb68gvM/r+98RzxufYMIEZJxptimcG+juM"
    "+RZWLEDiC4YunpfUlJGW9Tg/ShAMU4T53fk8v2kDCUd2FiMFcxFPEmnllwtimBAQg7nv/PxFcI0HtMKC/p0hIql8179AckaJLVWS"
    "MQ/NOr2+XhID0EWKBRKAFfdsp/a9ZVHmxXMk1rVeB2bQCia4D1gOMIqU6H0Vp0hOZMY9MjB0ME1mQRSlWVpFUbdM5jOwuWXp9fI6"
    "it+iTQmAD9FWqvro28ur6Ab1V+AHvWDrh+AYgTnkjB2+HxifI45iPFM/ET2jtuIX0Qhkym5vwAFVjppZqEz6Ji7RxJdotn8yxx29"
    "Nx7dhby3nrEYpwiOw/Q6rdrQ4pQYmGULcQLmmrhIEXe4WJbOLchgWnmsCTYCAipg76PRQNxFPCKoimVZ3aBdcHWL7WgKBC8zukWS"
    "Kee5SIj3Zrh4AxKmixYAKEwmyTgLlmIAsVPfkiGsuNhHIPqN/QJ6wePAtIK3aT7HJB/TM0aMl2Zv0QrEGWM9/83tiV1y/I/Oi2XS"
    "o5DsMnZ5gHlYdUtGpNw0SqdDNNFCeQasRzzl/FZpS7SzvLB28Ca5JQ/51iQHFd6YeL8JdQSUELHz0AZfFpku5glgkYiDd5p40rc3"
    "hTnojeGZ1lyeHGsuP9OaS7NmraVHsmTY6WCOtKwmUZbfdMmkqWGXTJdOlT0cQDNm5R2g73qsD3hYVvH1oouOoyVaGWa+fcW+BSUO"
    "OBgeBSGUDEA3D3qHvwOSFvDgFumMNRpUv6fZLIc9Dh1BW/YGfZLPZlikZK+l9YpTJA+5BJyQg14G12jfoqOdG7K3kLpUJGFPxgUb"
    "Mi5ZKxUjg7TM0QF8HVf4eblIJqPwOp0UaIOg7TEtwx5S+BdzdGoK1hp+tb093N4GbetvhEVyzCK5uUxM/GK6/BJpymkyJ9RtWT6E"
    "O+ySKpmdknzdJ80R/uAtfjRA+nt5k1ZXXQRAz4U7wSlm4Xs88h3HGeI8p8/2vv766z8GL8/3Ag4wxV5V3Ipe8ZymaNE5YYHhXSAO"
    "g/RquLXzOviK46ZH9zRo4cHP0AKDEsTgRpy0ARlRyHU8h7GQ0tIjzjnUhbzKBELnIrPFyQDeOZLjInn/dg0mpRH9PEHYGsEDpSms"
    "SIj6jZfzClEJep0uxC7AZw3+EjWbJ1kX/9ILfgh2nv4BbxyhWAzKJC4mV7SJ91ZQgEFYAi6OmmgbAPrk1Em/QMSF+FEEPh8+JXCS"
    "DLHfB08fflC4ygu9cS/4z8Bh1HeMh6X70jXiRZ7PlRHzcgAQDub0u0bwe3ylwY2jNXcNy9cZHX2n4JkCj+dbgmvsjOSChO5GCEjH"
    "AWzEHOlTIMxW/NgmO6e6QpTTDDg91coJoiHE+SWGjMRJxottO7O6ipCgHuMDfhQQdIHHsavsv2doIEVp896GjMgCfdKwK9OyBKOT"
    "tifpmCdnmxtpKeQ/fTS61WDOg7Po4Ox0/LyroAVtzAhc7D0igNFmh8d/cTRbD1KAJUYEfLmcx0UArWwcVaw0WjRE5kgpzlTJHWBT"
    "JQXU7CQ63T85PvxVef4v8HTGFWJOeYkOpJNo7/AEDHzodNru1bc8Pnl2ApZd3LQvyfrsJ4m00PAzTFgC9p7PcqP1kWZLMaSe+HR2"
    "WCg1ur8fkhHINojHIAjeFHVs0sw0edsPjKdplovvvhgFXeMTa2uJ+DaLkMlVnF2iM/zmCt4CueGNy9DBEHB0sj82EQAT2M6/Q+f6"
    "PUG3AKsjDrEpcTwJ0oniWSLAu4pLQbaIhpMlnHKYUcvAoqcAKoKNtuneGz7zmyyhZKaCKsNTIkkj+B7hDijHePFD4PQ/3xfU2MZU"
    "FMtFZWNKRXxD9jkcntKAfTegSN7b6fkBq7A8fpoRVnc7z2OQL8HFP4Cfyy4CRmEvXcpf+kgjTyeIKvcT+Js+wx/+z9nJsfS092hY"
    "Ece59eSSxH2KiT7WZcl5hXQk+hRvw/chNQ2A7sG+A9XxbrhBsqBjYIECjw0bqsuHpsQuRtcbyoD1TJYrKzikyz4W/hROy2BAk7YH"
    "lciNNTxK4xPlSW5r8wIMZsv5HKkySAaXvuXKqc6b18cwlXClwZjkOitvs0kkC4cuwVVQbsgiN28D6rnlpm1+giNUIi22AjWqZGZf"
    "sLhOUzDN58VtgEcWsutsHl+WhCEwucOUIPYPTsd75yenvwppo1nQkefTJ+PYRAll46JvsZozGgVhVoXqjiXYVJfHhAVLMGiKxr6e"
    "pTj8tIHr0vUh0WeofVcW17EVmO6BYXBxWyWltkbE4AGhOfhXehCTh99jBZHtcwEHjaskqMMDK2yZfvCK9DJ83ZMRxr6F80fDFqZe"
    "imQ3zZJo0et4mqDtBfR0CRGIobSXCPBfjXj8J1e+QBeeVBGsdgSd23CF1EsbLevH/WxyhYSQUD3JyUMFGVhAoWQ0LxN1MXFrLFKz"
    "dhRUEkaygqa4hz8k8c1UJADcVVdFvry8AvMKFhG2IH4XYp7fkEDaKr/GQcBAy2KrSWqhv7aYeunXPcMQ2qgbk+4BZgaRVfWvjcQj"
    "JI+Xn7KRbkdRZP56qigy/yJP907Hu+f6w/Eve4eddtpOvabTEUoOnmV7vVv7TOJtHG19afoKdcIf2xbR+uyLFeiZLExrbCj7Y0wJ"
    "GhsFR2OEsJJcL8DkjxgB0gm6tUGOPZV1tKa51WhPGg5Odt0OJBDjHqh5Y6tDYbNmOU+SRbcuotMlCBoOKrcokKXg9k1/J+HsSDIA"
    "r1foq0vrlNcsxVpJBZ8uKdrcLLKcydcaSjmqB8tsnmZvut4IsPpQTL2UOOUuEoGXZCp5HYXETDYhIt1apiSGnYWD9w7r5N3gPdER"
    "F6Aiot8gFH8Af33T7Q2uknd3g+p6EUpsAo/bnk2stF3abxVVEqe3EQZV/gaxFjSb7tdPRdskAwVpqjBk+IM1qOnyelF2jT0llI6h"
    "QxbXlJGhDNJd3+iwRLInePRK7Fa0vE8WMb5gUI66YR9UnWHYU5upu/erIPx7JpHNgEyzGy6r2dYfJJlFW0iJcXPSas24tT77gkql"
    "74TYaDSnS2Lh8lpTi6btbGFOVNG5H4gysTGBOM8k/EpyjYShBsWnt6IcQ1mq3Vc+tOgMrczX9872rPoJWj19fZvPASep+JwDfPXM"
    "c+BRHUo+nzdTGlEOZKc9iXPoKiEJQjvoW2Id+AkB0Qh0+/U7WI9wxE+AFp8UcEeSj0zoQPZBEQLqB1mCeHKAlA2sxAOdBgAP8QBx"
    "xQLRWZ6lk3iOgbUemhCxjlSTZYnDiGhse5cwudGzGKlSPVlHwUeMdeupY3HN+jIpFkWKYwbojbsBuZwktsoFvYa1Ja7uMFcb6Xvr"
    "7Q6EWoaIz2PVGvuf4WTjQFGZHg7vKb4mRWlCCh+ByHgBzqvhztPXinvXxzsckL0rzXPAPcG0nWYd00d9+s1r2QBkkILAiohHGc0Y"
    "gnQP5PA9++lOCiKWIR7h6Yl3MlGLfgXi+Xt711Joivg6vkSwb7FX9TChhRhJP/eV6AnmdY1YYFJEBQ8jTAU2lWk4IEEpSLPp4t2G"
    "dlQ/0K/lGQpxR3D1pmAvSbiUw7bEXWU9gGsYvMcw3alRIoKEKLHS6dHblCSKaTe7fY0U2Tk6lV/Rn43QBDRne7TI0CLwscgRUzJS"
    "DSbiC5dQSAbq+Ip0HuJcgtBZIFZSTtKUcJ1+gzRHN5DGU5gspTACauth1DXVsK4QlRal08fBenCjZ0hM1IB+5YMa4iMeKjgJeSe6"
    "EMOFMjnIhMdWhSKsZABBv4UaXiJC3yX7dbsYEktgUIBHmkBQOup/Cw0QEHzVxJUI1BbJjNCKFbn4zZd9ejZwJNN+ZUQjVEhkcB2/"
    "i+ZJdgk2Q3KWfLvzlJ6mH2I1moN8BMQbifVRoqGa43zEeqDzMZ0kZZf+OyT8BMsk/wqq5WKeEG4zGAwIQ+XvXTFptKd+0IWmfdJJ"
    "j8ensYF8g5hiEs7OAJXoEPHILTBV3QZxUcS3wpOEEU2aA6q/frryYFQOB5EfMbyvnwZISbtmNm4kW8noGgWvyNENZjTwlSLiMiZL"
    "HoA0oW4J1J6ylZEGRChYDGGdEumMvvtGiLZw379g+JVFbFkSZ29xngSb2b/1Oiyz9J/LRDb7L6sBOqeSbEoXQSFD9NYkwjmEiXfx"
    "3yI+Hv07NE0mZLeN4C35QPUCn98umM9XxBT2GpQ0Y664YzkUEo2WIBnN4qEl8Hwf7GCfJP7lh0C5+e6/hdVhL5LqJkky1DPwpfdK"
    "n3fq3sbjWvY2jurvxjPl3kEzXvEH94FX3HE7vG6v2Dkwhyy5xDllvJBVZvGivMrNUx8seOpBAk/qDhL3ucFME8YaQVKMLnZ84f56"
    "NVfFzuCOgIjhpxI/yTTDtUCuIJIbBUBBOKsGEq3hQpUShKjcY1FulwgZ60vxI3iLL9DpHxEXHccKqI5CkdXleqIMezau05A5asld"
    "uUiyJNZ9YrlyA+lj3iaK5quG2Emgq1aKIugqWBgg+RXC3iGARH4uc2EsROtJR7q9nizG+mna/O6PDCCagTQftZ0K00iDEWYjo+I/"
    "pXvwXNGieVa+Dh0gEN+7U0e24pVpxtLgprZsCTrEA0dCERWrbmjLr3W7Jv7WQjpwddPyFEGi2/7JnvlvBN8CHbe3fAdxeMSNE4cd"
    "h8XUqdMwLzpY5gqr7LA6GV8pURT1TcxbKoY1VQPEhQWba5rjQ4SMy7zQ9clAs7nBN/8RbK39h3a0RxIL4SRYiDuSXDzi/uHmhuO4"
    "EaZcgRLLJUB52oPrN9O06BL2QlVncgc2yt8wcRDSg23n/yW7IZSj3d4ziXnQvnMF2JAwvLKs4SuUB24WYusI60FObvtRbwZ2hxnj"
    "UE+HasJWPmOaosNHYpJvnagt4sOMXRriDsGCa0ueR+PrcVY2hychdOxqZiFYDX6H99yya1vxMZNtmWxKdDkRG5iuYsSShZFQqBri"
    "EgGMIO+KnujVuqQb0iR7cBhHPK5xMEsqNNMM9f1q+7URi8B7NUKqBAoo5gi7id7uSIOr/SVzqccvRoEtU5clvKGdnVK9C2y3WRIJ"
    "kvJHyW5JfroLjc56lhOMW27pxFnYoXX6hj9LXW7qItKJq0ovr6oki6Rg8W5PknBV4uAcmAo2A3EeyNGJ8XSeZiDZ4PiP6xxtGrDM"
    "dnvBV0Fd1i75lCtuo2kyj4HofbJ4CV8VDgEElunChCzwGtMwZB/rTjB2kpZwr2vyR5IZECnyo28H2g0SbaGK/CaaxSR6dMQxfZrf"
    "1H2kb0HI+ZheZsR4fHIc9tp8DLEzEQd3m987VAhfyhKI81zyrJCLxfw2uonnEbgN0Uzmc0hu2TF6cDS07zMBbd/6fnoRzeOLZD6q"
    "E8P7DbtONvzx8VT+Qg5MtignLOtnPLf7tWUjkqAXpyfX7nBlo9ncrp6bXQSJR7C4kNIP/L3JNAQlHAyyCGxuDse6BA6gsr21TY71"
    "ixPG6pv+e84S7LBLUWLSxu9Z26qc4TrNuk50yE2/DJ72nQ2bMv/Zv+y56BRJHsukBkl2JKjSjZEGIrSJMiTGTbUCufs0o+lsfdbc"
    "8NI2QVPwx2OjfRGMYkwE/sE6zSPfvhtcUKKKqilxxbGPQxEEQOLgZ0lqX9Wdoa+Hushcoy+oCLkFa755Amt3EZZFaldCmP2nN4jL"
    "CLXCIkf4I00GHXbWlQYMSaBWCjDWD0FkMhPcGtLRwQxGFsnB1o0jxo8d2juD7brIvtbSRo2wIDJqm3LGygKKr3CyYwontk9x5myt"
    "3f1qM/9euscqYlELntrAGVFPq0szw859n8T3cAq3OIE3h2b1/PQ6Ozc8+GaRaPBycvZYJH+rat3ukFc4LlV0ek2qujiPbWq6wxw6"
    "I9F0U5JpA1xsDRZC9aDAHp1Z+N5sd7eFdDU9xKiueXl1LTfXg19tSnU6E7A7JASOL96OmTaVmPIm8yY3cUq2Dt3KRNLTybKCKSJY"
    "VuCfyxyclCLUUIgG8vmiBjWfjQ+R8kG+7f7Y03TVrprwSk6rJWNYPZx0gGTj4eZBUrJx+QMlxT5uHiY555cHSOboJJRaT/8Xqkeo"
    "VpfjT6qpA9++Gwfnu38+HAcHz4Ljk/Ng/MvB2flZYJoqRNoZ88iHaNvz8S/nwYvTg6Pd01+Dv4x/NSVA4lnH7WCk45eHh6oguAJ4"
    "pQWavJjiGicHx+fj5+NTGahg9+X5ycEx6vlofHxugiimjdZcgTR4eXzwvy/H5ifSXlI+MFsqRN7QVqa9hqakeElULAXQLlhv8uIN"
    "hCfhVtapLPIyntc0IcnL2UDme+5va4KaZjinBjrPxs5haXb4KK4asVXFhdTSosEspp5diYTTrr5YnZ4ahLJUqZxaa7BgcWg3gIgk"
    "/L2/QOAeWxXEDewS+xOWP/1JP3hC8qfDTzTPOPxo5CZ/Yrc+PbHnE4cuzPzWrj60FNfKxzyjtfKUJrBu7JBnB39iKhs9JwZVcnVj"
    "UU29DwDqqffhGUu9j9+z1Pst4aFk88NIzsjSko3y+lqb5aKsW38m6ua7D8ZwFcZWP7zKJGv5FiZ+Gn/e1JqLjXJ7CwA5oq7b+jZa"
    "cGcEgf3NRw5Usij9eGgzz6aRqS6uV+ZzsuC8DkbthPwZvD//ZnU96vi3N3N+dnI6Pnh+jLdHVybnXnA6fjY+hbT3styitjH7IztF"
    "7aqvEZSbPQg2xfgPZuZ0wvAzrVsCP4oyJU/unePQbcYW3cJ3OD2wrVgrU36MAps3q8khb+51404rlzipvieXse23+k2ynPv2rTP+"
    "FSUipHJM0nnKBIi6hlA4x2Orb3p3Kv1Js1a646er0sK5wdhq410rCUjBEyEDKaIPl3iQbMaFmt7KW5NVz9ysLKCJ7uuIAu5PWpzd"
    "H3brf3Q7WtdxHtHm/2BH82Pe/M3H91qnN0cMreZqC1VGEgqc7KtYX6DTj0ppwABXt4vGHQ0m/kg1jlg2W97YxGuT1dtKkCRdYrve"
    "1DVIPsFZ9P1k6E9jGyqdCRQqfbEDUn7v3G8SabitBUZ5QyKZ28oauuwalhKH0IlZ2tDVgVHmUPmcqwkNH0s6BEzBUeLQ1Y1R7vCe"
    "bSEHx/vjXxp4WyQeMJ5mAHBybHymKWzsUwSIJxycYomtKaKyl21sQdy8Gjlu6z+Y2G2C89SNKO1OpbITG1b55uD4bHx6HpycBmhn"
    "of0FPOKkzrPQhQQR9MKcMfrPu4cv0SbsPtFzRQDBuco6P9FQcU8gUYohpZYimhMS4Np+SADQNqsg8h5TLK5lAiA8/faPT7e3HwoQ"
    "cc6isd8b7sa7h4FCPsMlOOTHDwSJpBtIgEhP7/RdYwntGe1oG/rk6OjgXH0me/5k97wzWGZFD7LhGy/ym9LDPSq7SAXmgmenJ0e1"
    "zsa//oSOX+xgRMeoGR8U2vmBQoY6OSiL0tNu1BD/K+Rtc8YQ7dNYAncAkTNftBELQkOhcJIHKDQ5R1vYFhEiFWZ4D3FCCOsQ44XT"
    "aeBfdl73SKKCHCcqgFW5k2/GLmNIscGT0uO03TrmQuU+Ef3oi5GRUsc22dZhYmqIGA4Ma8isQwC6c12BQqIKIkB5m/O6xJxazCpg"
    "1sCFFgW+Wpb6sgcAaOVg2QJkt11lwdAuwEnhydaB1ZZ2Elp0EwMDnN+i21vjvppGsAK5nCR5cUmckIakSMZXgAPj0q/EnTZ49XOf"
    "3pJn9XiCqzx/g5RTXIaTlDclFS7Ze0ijltAc5Ju9E0oDdTjaPO7fc0EKb2a4/yBKWXesTv6mq/TMzd/UTnZlNbXlNramhll+U1Ne"
    "Trq8L+r3QbYr7dKsdLdWvbbJ9WOcpSazF/7ui2rfd62SotBhpWGM9DtmyIWR/EV5zdPAKE+lznhQhtEPe8O7YA+krxV3pNGD/Jb3"
    "Ij+UehJWVKMb/or3wZ+oZ4bog92o5ZPAT+She21WxsKZ+FA0nQ6cw3BgOAdkRWdL5yVaHFgB2aakgn6Innsdq8mJ3VLNkhv0Kz7c"
    "Q/mevRwxgHa1HggQqgjD6SRCNX4g3MjlWBwOSvLRVu8yOUraGuWoshqrws5vMfvJfbr896Um9pVUylNGHv0Yuu9FaVus57gHJQXT"
    "WRvAArCpNF6dkWUMSKcsdqe6y/iS9pzdcHnMjSsZza/Exn/d9/xC2eTeX4ltXfOJe14YnxRDX4zoNIe1Y6vbXi00XfshJiiVEcdz"
    "UPtvIUegVPTbKDpdhrUd9+qu7YnUGYIVYNsuEri7DI9eN1qto+jxm5p2quqkZbeGTmUjlCTZKubtfmAVQmsDDPtBI927e5JsRgp7"
    "7EvxHX0pRM/dk4jy6EvVxi0RcnbGwBX0H/uBz3876O+ea8XsUHoujhvrftpJzSduzaTmoxUowtmMU4qzhUJBboS1oaya3StTnJuH"
    "ESGgsYEqjUKZIiZykqNclYpbj+JKbOQ46Xzuj9OcHTiVX0R9bRkWCVa61y6T8aiZpoVDRLoIz/wejj0kO8RGDUssOa1Gtdhl7GK0"
    "43XpPr/hEpeYJHB7CT8KLsw+aCZHbJqxpHH0PV4KVRJVbFIHkHgPNrCvUUo9aSU9n2VvgfXHLUqbPco3pQdXjIUvq1k5lrEpJYw1"
    "lWWrPs2tIGyh7dU7pMxzwiHamKROimD4gIow3cmYrGi6HaSXFVE5yReJ5oHS1BKbDg31lF6eH5wcR2fnu0o5Sg+VeZm9QTPN5IRG"
    "rH9FGxJOZzNZqnjJFUzpWaikSN15+oeedT1s/Yq3UsfiYdhG93soFWwTHAetMuU2bn1GXVelWrTMD4BGZvDGlqgFOC5GGjNDh2Lx"
    "X/fc0QLkZjP+gD2ztZf0Cb6blRyMoOJDT0YrbCflDMBj/k5+yMdl/DC0wqmIAhQvw84qWoTC5uu2bbQLFc7G+9H56e7x2QFs4DNs"
    "J6bD94Ft/55kUFO012uPBKdsMgupSUza9IK/D4P3FIA74G7vOdx3oSNbiTOirYV9QbctaHHGxMCgiCwu+4LKP22SVZ1ZgcdbRCx0"
    "Dk1CMidCGP7p0cHx7mGks92O3UwgxQSNtHyG7gtV0o5UXth3JQtnMY80pQcwCzhioAi7hg8E3+5Y2ZKJH5zMFsaRrA9Dp72GrWgt"
    "b3NgLzRvAYV++gJ8zG7tuD/RcKH8Clf9NaqMaMI/e4dqkQa7MM97EjYI+kR27Y5cOeka0DUT+HrP+n31hIYqPnl914w7yUpKY3si"
    "/qpG9fBVQaQjc4T+b7By0RmMOIpqm0uRr6NGba3nT0U8xim07g9+pNqplVQV1Zfqi4Yjx7JvWPp+RaLtbNxKF4pdjucAioWw1zOY"
    "SDprSZSrch4JnIQ1WDfjPKmRXdxsW/kIEV3YDo9g93ifXIQa8Ss2gR3U8OR0f3wa/PlXHke/e7a35uGjBijw/QJaV6lM3j5Vg4Ja"
    "nUvkSoCFYyCatV/+DD00bSB+DX47QYLjmzUBwI0190pk1eitqLPH8ltaL/YhtI6PXNv4bHzOKEYJlxS2zNGPwpo5Yj98tVPbKyFO"
    "KbrVTptN06xnhl3GAflAr0JpSCRV9NvsUvCYSboA//GrYKfuwjYVR8QTizgCDizxBWN2Vq4p2+zsJKOMTVFQe0XCtd8km2GrrSdM"
    "iR4baF0nAyVm4V/ouKhYGOV+1Cz58gMgRdmaj37zsOfDN/LZSzrkR4OyRWo6tJhORz923BvIwt3FMJuy/Otrv7axWqC6xpLs4U5p"
    "FHCkFamFRhzhJBbtlWpvqfEqyjxhU2Zxf29L/che5nUlo5RI1UaFCnRkZ5dJ2e3pedpAeNu5dwvFo7H910QHWK4yhERllMiKhEnY"
    "70iEvotn3sgZMZtJsx/CbxNLTLBpk0oq80hWGTfl56jfWZb8yavJV97y2wr02FYhbEmfNh+V5WpLg5CkUZQ/s1+RxNgfWfZziGcN"
    "6rNEgB5BFwKf7FQIGz9qK/g0dqjxgchX7/DgDK13WssdV3uESMa4zoY2js+mWWHDyJvFuMBVs1VWpPQWDLX14rVYuJ4r4cimvMSs"
    "u3U8xbSP3uruWnrmRnmxQIILv7KiRh05Q5pVN6niqPX2imp1KanEfRQXb4JFkebFFpUCoTIcNzLhyyjcWhaQEnJFgkVgUSCt3rmq"
    "+RRxJTc1T6U8H/hjK4fqkostV4Mtfkl7KiFRM7LjYGNt/JlcyLIWvXxgj2crO51Df3VFl9ZofeIwgkuRhwd/GUuZwv6vy2iClUNL"
    "MjYkydtVRZsBsKVO6VR82toItUtCw1rZ0eGotRpqbNbuGmmfe1Oos1px/zREQzeb3texA9+nLbitPRivPcGONCQS6OpDnu1L/9l4"
    "+hEaT9ddibpV8LP4mVY/WzZF0xBYi5SNG/zaGf1chr/OSqvcbVALvGx5XnY470b1Moe/6Wt1wxs92Q3O1WmpjvU+mB7UCnp7fKuw"
    "ZLm/1OwGehiVpyZl8xbXaTxC5Q/ZslJBuRbY1fSqtjrVmifVQ1qXWhHJxo1M7VZ9TaLZuLGpFeWtZ5JZxRyjaEkDQk5dHlelrrmW"
    "d49q6Pzr1TVxeqOYYdhD+64Jn5Zu8SrP1ZvAyis15aj2UsszK2uS/wqq5WKekMDmwWDwWonQpplmhwGzAIi3ehrcxlvMcj7cjzMo"
    "W756297eUHc7t68aDqymAu0K8QYMHvXXemzXkNc3eGi5ltvOg3zWbUjhXGO9kZttYDpGMuhRE7T6F+ogFE69kQYphxKXp7cSi5J9"
    "uhEopbkVIqVFa3Ao41EBoQ+7GncyjGPRFGaAWAI4q6NlNYnQz2q+AcGqBtXvaTbL5fg56SXERM5mONLaHpnacItCSr59vUSIvUhw"
    "wbTfUT9b8U1caFc4xAQsYEkv1wULcNMMj83UKIDoqXz/Vm0nJt7rWGQM45I9ehc+/J0MytvBnEw6jqCqVxRPCH12uQAUNln5hdt+"
    "A3c8pFtbD3PTQwkPJvXK6NcNNz9kYFVL3xcjSSppHwbBNQTSCbvjDbXUcTqasAkW9aI8gKMIQ2tAxPrxgWmFjArtTZTqIeV5AaI5"
    "53ztfYhWKRbEVf0607HHdWC/e8X1J6bnMSb/gTILg+nyelF26ZkDGSAXMa5LWo66YR9S0wzDXq/OAgFMso2i7JFNomWGh/ZpJMQX"
    "hoThN44iBXh9Yqtx4felOHNc7Z3pQgSFeuS3WD1qnvMQuvkCKm9BZgsW5QU7qjmjRa/5Dho/bj0SWagiEZKwqVDQZT+AyNELvh/J"
    "UojUzFllm31rchcfFuwjT6UZTk02W6KpJxYWzGMXInqUtGfFO/6Xz1x3BV4eH5wcB7uHh4HaJ8/Ob+3s8ODoADX1Y+gNeZWb2LmB"
    "p0a2vtI+kAQTOo64LkLzvJAqjH9Co0P0qGQsLJlhwtdlSG2LKyy5LKtq57Bjhb09iG0WdZV1pHO+9+WT0/LEfFguMXsuEZTSNUqQ"
    "hY13Ye3+M12adYaIsuuz/dX4kveFWinxZZyRe1mE8ceCrHEShvcG7He+GNxkEiKRerrjYSnu+yYk8pD5tKxDmn1mZZnOWjKrLx12"
    "/aDBfyenMLKlJ1o3E5FUyYkmJdpYRiJ5qT6StEUbUwKaDHwfrbLg5SqubeB17UELFZI066aAoQ9xX8lWxdIVQ7LydSH8q3RHalPb"
    "1Nvfuu4mti5izdbwO04f9y0afsyKGyUfxYWa0Cz1Eba58dK0aqq/2JTA7F/Z3MTO2bS/0tLmOgvJp7Mhq5oSKubawPKE2wrmq+QK"
    "MxV4JYfMCsHfuBJMo89Zmqc7HRc52wynsqv+p+6wNpN5pdjVsPMIfLqKhKt7Jkj50424U0lfTt8jed3O5wh/pKRa333j4ZLU68xt"
    "zsXqIoYad6vrkwcJnX/YVGAfC+taO+kYP4LrPFHY+2kJSf9i5MgE5ryt6Ba0WJoByJdE0gTQ2lhuOytPYkR3I/6O/Nz8TQ054360"
    "h3bpauiXt7jhsGjv5vJMkKbg9AuRDz1cx8VGj0jWlXVgqAxfJtJWptFbkg+Ax82Ih9iGbXzaaS2W054R+wg3YkOSHJ6kNJvNWcuD"
    "IrCOOc/jaUlmbfeZ2KVwcoBR+xoL8FrFFk97omvFLLPrucZtEXgb85BLBXskbEsdsbxd9+InF3mEPxHbJschN21iZiqh9ENbNr1u"
    "okg3UKRSiYS4id3AxcT9E59IJenbXGdxXWNZOYMJlTj1g6fPeV1g3GmhZl799H2EOn+rk0tiQY/H0KUbtSjH+JRMWSbe+/Ximvc9"
    "jc+2rQ9p2/JeK4eJi8rh92fhYqfvB7Fr+VB9XQaCj8zK1TrXgam8rJ/qgMjRkVRMF1fG/LLfYFhyZSmoqV/mUV8Ky/Qf3Y38enLS"
    "AxvgTJECdJCi9eMmUmeyLXYvN+I3fOVWoEgSK3mxbfUYb3fB6asd2yZe8V4rl/Iw2/a5tNyrPwI2pdpJTETRzJxsRNcHFT2sNhXR"
    "sNHdVisRfkKXFJnFodUNRTvGW9xRXKUDmfZN0m1xQZF/R+dO7wDwxFeP7bqidIgYFxbV7duruyddpdky6axM8878IB9D4oInLDPJ"
    "k7UyGNRqXi7ta102Lfi1qIlmYaefzEX7FqxpTba0xrXpVZlRLSN6FFf0V+E0VJ6nn64utvMb85txUytFoFbLWPaQ7mW1ntJGcpSJ"
    "Gkxur6tUk2kNb/PO0z98dsN+/G7YD+PR++wl+vfwEl0kgsVPH52HyC8fmf+p20Jrd1vbbT6ZR+2AoZgrm5wwn10vjyuK+LPD5d/F"
    "4dKYl+qRuVxknalWpL3/uOPPXpl1E1AX7IopD6i4n+xXtKKd9rRcXvwDmtvjkmujloukXM4dXyra6OaqDbNyaSnLgH//9YnR2XOK"
    "V4nUfWDVQCExDsXOn4IMcRXISDODvysixl0mWVKkE0D7Yumdlnu9msd0iZmwfDreGx+8OI9OXp7vnRytWvGYXEumXT+adF4q3W4s"
    "Slvt1mkjUJttIBr7vqLOfUPNNx9frnCHTUbRi17rAudFq03Eys8UhtaxSDUrWbc2VdbbZItmf5p5Uv/AmrXMbBauZsPi5IW5xQyR"
    "2zR677hE+ya5vYveL5fpdAB/fdPtDa6Sd5IO/GjSYbFZfarpsO4tnZWe6sIvnVW306B4yb7uUYs6yHBDQdHflFLSuCt3VeRN2ofC"
    "ORQUEJU9OIJ4MVnI7aLuymARV1e+BiLoH26BM8XtvvJw+ca57I/P9u47A4g+5+b8XMY36jWZegPwyouPMZQlN0iG1LOHBBfAeUo9"
    "RUzbfEh1E/tCvv9zHzMjI4spTfOEXpUgJ8DvyQamB/05T+rNzEPnXXxrphVaoHeoN2nZKF+rcrxxQy8MsVVoyB3TbpkVXyUs9gNj"
    "iW66CzhpGDo0ilhhmm4gDASotiF8gElP3F7mwtKV4yBs1Ymacs75qQyn10e91VZTcu60OJSTeZ1kvN5RqFOQzr8VUuI48j0I189E"
    "6ZP+7H6OtMb0kQ1ZY6gqb3/ZpAB7KZUemppmJOrXpaWvKe/RpMg4Leb+6Tp54kmc6d0j+yRSYNwbGsJwUQMwz3ixUGwPqW/ITDMN"
    "zRptFurojQYBpXmTxq00lg+jxn5r68UZbZ3aqt+Zcv8ZMUM5saUu8HikseTr4pHGcpOp0Jwmaidz2EhCNGprJZ0JkyFjYV7sysay"
    "NPak3u/zqypkYT3Uno99LesmQqP/bSz7mbwuH0n2M+0jmRjWyZhWe/75noHe56DnWeh1Hvqdib7noper3MsD/XjSRJHlH/CVYRvy"
    "w1x0c4dxeEkpGdiU1/GtOmRUwQlqRFSJXazoYeXw+xf5NQ431seaPlbYCokwA35KnlabS/QhXLGbcKy+IEmWA2L2pIwezeOt1QiK"
    "RGao/YGDTWKy2MKlen6FqAxModQZu1xcFvE0KYM4k/vidm+kOJbLySQpy0FwUNEky2IFs/ktFtXjTPh7q6u4grg9DG4RYIMweQgq"
    "KK/OTFdfeEcqoK3gJpZqNv+Ju4dRLwAEpFjA2VdJmFgfbAZ/ibMLNBq20IneqNEMgb6AvRULBA1k1D6csxksh8LA3sbH3LHbvzX7"
    "Nl4LMt+ykUhUQfbTclZrYonLF+0Bz2dvs3U+mk5gdyZ7oPejdBdvqAJ8k9Dp41le2U3/afqhP3uP1/YeE4tNpG0h5Vdc6Uz2AYud"
    "83ptb/QXLbzRBiCKM/qLWme0+3uM+DpB3OE4YNaliHa0XoX52voedXc/myzmjTqJZvbl02k0//qZ3FuYH7xMEG3MEK1MES3MEd4m"
    "CccOay7d7WuhcN/i9qgKZaNizwpR9k+Zvb31h5qk1vp7I2tm2w5UUab15y3SC9jGljnqKl9r8sLrVehFMeWPmk35homC8VNuoaj9"
    "1DaZfuMXgsGOfPNg9O6lpg1ljsk8vUwBYTPqyZYUL3VhXO7Xz4FInwORVg7dsIYotQq2qIt++bcP/WgOSnLEIW4iXunjiSBxhpAI"
    "8/pHGEsys0yjZak0cUO8Ia5VXw9ic8WqMBKpcPycZNtFTJkemN4r8tnf/dnf/dnf/Yn5u1splg/r9r43xv9ADlobx7cZdakBiZsf"
    "aW1Urgut4+tHKpRw8deHG47YD47b1lJuMufafpyV3bTUbbw2rZTVDUjFPy+H2EtqFgf08zID23lEMEhpcNXkDpvi5WKSaxQBbMlI"
    "mrJD4K1gwdYDVqV73NkhNDfuR1FlrmX4kJYWwj9h3op59lZOEoEFcEv8S/ghUkY8nuWmfGXAGerHuc78BGjik3UkoRnyPkgNw9Yu"
    "pnr3knw2us4cCWWrFjE0PUmddiZkZhi2G37XCH1boPMpIVus9Ah1y+dTyPqLjoTGyK5WEWBIrlLDvgAsETB1lZZIy7kNbq6QVhVA"
    "Jh56NQMHXy3QoZFWkDCpzIvgBkKvruPijW9CDZqqBwGFwAVzXIS0oQj9rLSp8hk4hAUCoKVKcqyjrQBmOU3mVdwtYc9My9H45/Hx"
    "eXQ6Pkf/4JTa472T4/2zniVGgYwVVck7LU6BvOg9vlvumIigSLPl5DZGZn8WRYowSERqjwy6zY5kebe/jefLhCpQnN9PqDwSXSdV"
    "jIgxbrpuSXjEm+R29ITskAjPdIrWv8iXl1dPauyYqqzkfecMYtiC7jk6e7C01A9+hpnQn8t/zhH2vx7gX3s4sO/dZFMGODxDYEMQ"
    "cIRIiu8jYFtIximWTjsbSWeDYLGQzj+XRExaKXUo+pwsIpl5xLpzOW3IgmXxNS4iwNadrJ1lsep59xxROVaVrtLLqwijg5KrPCu0"
    "uD2QoJWZJnOE/G1nukujw/VzhFOE7Z3sHo7P9sbdo91fOJi9frDd0zcDQUrYWYV4W+ReILv8ewsyqO/Diuaa9hYc1rQmw/9gHaXF"
    "CJ4d9FqmSyVbDjrcIuNwxiRtOZd0zCLtost5foEkRiK10pz1aXW7mqisMrgRxp/jGmgWL8qrXMbTyIYlz9LUrcqsOMwldBMkbweM"
    "9KFy20ASScH+PuDytrUT6z5B3Vgb/8/JwbGcFDN5F5zAg4GsZQRQuHTQeJmcWfY48Ijs7PYR7puWG++e7bU0ppDFbVslhpIGmpTX"
    "nvAqKkPzMrj5G8+oLq0lGN2+l6Wl2vqyvAc5ofAACVZFVYIw1BW6l+OSZY3zVOCEl+Rl6wJQbjmqQMCfC6RxvbHxU9bn9yPCxYZ1"
    "Nz9NBojEz6TlzaB9tH3Ox44NQCsYMWqDWkUuuqKQ2ykLIWeSL7PKIvvQkokCcoaCLYIBVwlk9gGitO0ecYqrXa3EloETgooBxIsh"
    "BqacZgiboHygFuEa12l1w65FNgWrLhZj3TEqzfKpY4VgM9A2vX7PT/HGdGagezXN8z+CrbX/0I7O0mmyRYxyW7MiQdohWGomYMiY"
    "lpsbjCvMUJBMRFf76MxYcRoC6hBJb4vn8/Q6rdjz/fGz3ZeH59GL3efj6Ozgb2O5HWVYTfetLCey9Akap8XVKwy0eoeA6Ndd/Kan"
    "zkJtiB918d+9OuDUr1iDrqWlcnFCJIuX7CmYx4s3NL1lfQkr3/SW3F4tjg4BEFakmdUmnkaC5LtYQxO/q4MrRnd3pD1qRpbih2Ab"
    "dpwNjXSyXby0aE+uZZOnVhQEUHyF5hPkM0lADXv1dqtJPp+TGUWL+DLpvnrdJ+AjsPB/z2KkGfVlvoGFzhQhQnIeppncEyeMThth"
    "FsdvjsTEbIZUDNqIAGi9WpIgRj8dWXDe77jZ5SIu4uuSli6EXQWlCykWyExVjermKinAQRyySEMk9mEvGfsdn7U68QiuYEBOOvwK"
    "9aimTg+MfgS4rHQNb2ybk2gEe/6rYEeraOMvxc90L7cQocmh9h5P4i6w1T2kUZg2+YPAqa2OS5pVTa4IeEK0Vu2kJfFgDI3w332L"
    "aAbm9WKk39Rh1t5+Z8VwX5tKRihOp1d+nF0m0mlGK34a95mbq/qsdkX0nnnnmjeUiMlq9etUTfVn1gVPuRZvEBFoIU53gX35o4vb"
    "qMyXBRixljAfSg/KMw+CkJtv5tqg0qU75bLcagOXBT8G8vRhtk28VsGb1Q/WVRegZ2Wwphn1Y9gBWJ4XNY/vX5zHETqfRXlMHRgX"
    "hhhPnlKpdn+8d3C2pgQv6uzgewKfmvAuKkZ9WrI735WfmOhOOECT2E52gYfIjhs+rLiuX6n695HWjRIvDy2s8yKfRDbTa3quIarX"
    "ZZb4kKJQYxnGlSWhtoWF6ooK3Y8Q1Dz3+vpD/iIQC4x6AAmIJ7/6LANJSZJ0Keg+KvWwQDSzVM+nIQmx+X1ighDbm5+YHMT4QJMk"
    "xHaChyxEmz6sNKTFlP77CEN6mOpDy0J0fCoKiTjddSUhKeI3CIvJ4pEIQoy7bVwOahcUXRMQfT9CUOO860OnvUUg7yjodQUg1cD+"
    "WQqS84C1TIf3If0LDyPUNEXs1ss6noHHWo5diCS0yD47QvYxxJ8VIn0bo3xbhPM6Oc+Gw3j9Q3jbxjqvH7pbF7arIsUalK/GDm06"
    "MnntiPxVo/HbBTNvJgK/IbSsIcjeEU9vjbH2Dp/2D51eKwQax1wrdOQYQmvl0X2vNZ5bRVbHb+N0DvqWurcbwVJaWxbI0q3tJLEd"
    "HvTksaTEQbO2fFCz49NZoC6mBawm/OoxQxjHDAw/ZVs6Mtcc13LsMVC0Ma/TLL1eXkdiyu2PqKODY8GTffix8zwyoLFSDbBeK9xw"
    "TtnIxz5JsCO5uqm9jYgpTNuieoIDx8p+r33XuLp4IZ8jKMj6GvD2A5vMqF9M49uB6I2UTMqw6ev21yjqFFnj+oT8q5/eu76FRvTg"
    "sNLIFwyIr0kJ69bX1S2bW4w12sWHRouNO37mwWLO1BB3Zr4xARc5AeVLF5sy6cAlaMIOYE6vhnjC6gJexWV0neM1nCcZaLZlD60h"
    "bqlLqWVeODkLHerV1s5r5aKCjbHwMYGX0A/t7MLrHHIpGkaXr+QmPsq8fNOEwmlJikEwYz6vu69kciTzc4qlJo7gP/DmI+UPMjQI"
    "2CGRJo0vQZOsG7CuafY2Rs+y6j4i5qMsuRHBZG/STLLPoX+HHY02ZuF7aHTXPu26GNJlLiAwiMekEIq4MKJCp6r9OHM4mDcO9qPT"
    "8WC2nM/xI3ybQnQJwm0Y9hTrBvmUinPQAD8YXCKaWHR31DsrGIQWzp1ZCKuHngXvlT7upGmGJjR08Kd4cDei6yB5lhcX6RQNQgUz"
    "CQcXyTzPLktInRgjIeOKFEOBTkNK2P8NYQvpBMnlV/lULJwaaGm56j7kOtie5Rx2lOLBqynsbLSD0/zmtUF9TSdJUzBdbUpGZypG"
    "RVyUyLguA4pqnvTFkGfdIp4CRS9cJO7fGVWGSBoPbP8Uj9XEKE3WTjkbSlPbdlWLuGugqSXPf1LX1ExaAXISRxq/f4KzPpz/+mLV"
    "qydY1YEew562NlKyNcpfkcQrqz5cBmikaO02rZLjUpORbAVdCDTemS0lNFkieRVq6Sv0YKakdGaykVaxHyjXfxmVqmdtixyUvU7t"
    "5doaBLmrdljftkgp2SqVZAv0m/7ovO5tbR4i3+UybUXS8jlT8/mUfpnRLTGYx2g/5zfp1G7abzAqEZ4cTNHnuMwWtQ8FMdcKlDo6"
    "/NqkPrjM55vSJa7N6gtIxCOde2ZZOcdrKWWifp4qsluLatlrFPwgV6vVQu3OY1TRYJtKOdfX72BIaigKIZrVFIAQjZqLPUjjNhd2"
    "EI0biziIpg0FG+Q+64szqC3rCzFohteG6tn1VbMbq2U3VsluqI7tVxXbsxq2VxVsj+rXPlWv/Spo9eptel5Vrb2rWfOEXixvt904"
    "gPQpvuhKeaKeRaIzxCA1YGMd6ceMibScZprcI6Xk9k/urST1dmXybk6urGXwlnh3q3Te9jTea8pN7L+d1gJUreRgOQ9W5d73JXC1"
    "ydn90ZwgMm09uuPFXv4R8xZBTKa1CBFTxNfJJXy5JSlZ3CI3DSO21sPgIs/nkuykCiCGZxeTtSBAUkrESZ8dzbWKv5Zp0vq93EDv"
    "oeu6O8lnZPo5kUws7UZM39ZxpfeOo6fnJ4+b9nmaGDLBkAiWG0zzhMh9xA4HxTyggsTbhBulXFGGEe9SsiW6SAOHOAwh5ko84wZF"
    "K51kyzk2KBP6QCcZCa+x2ayq4rVCMCSegpkUAfu8M9uZqnj5jPCP+hgrOI/JcMS0SYy4kkGCxkw4zBl9h8HQHj7hWu2ZtLrvdfsm"
    "Sc2DraC26AlJrsDzqDdBiiVX7iLbVldaSnwH2TCJkQvHtuUlFuV30TzJLqsrFjT47c7T5tU3lo/Or+kWNZ6B6/Y0/rvmzjT7weIe"
    "4bMYiR9dAj+lFQeprE4RGPw7Bx34rbZesnjtBd/ISlKoyNopa2WuzUeBZiZJdiX8IrxK+AxwFS+a2N5qaUWvaDp9hhjAd28zE6pb"
    "HaUQBOUlXqFjvXvCuRxgzaD7Xk/LvzYmuPBElYC6lYZs+GCu43mLJfVJ2j6TIgH6HgYsK7R4RUtU2F7hfH3J1Cv5NBNR/TJQGxIZ"
    "hQJSDFJQ/ayEAqUcAwHGCbU/mdglV4JhXsYlsC57waHAgcb04Q8MyN59wsaVdgM4/kaBjj+9R/BsDkOz3uYKkrtsWzUchsLI2iLo"
    "XpGtVBUDRzPokjUpGm910rFkKngUes3MJp/atGIaG98z08qBAcWWHNOwtbAb6by1VnnakaXNkYsNaypKz9pNx+aED7JtXhQVwfka"
    "sypGRwTkh2e+ND33g56mRscniFAkk6eSVuZ1355uRrp1kBdvaPlwZ5+sjdQdfyR6kis61vWmVn7kPSqPJYpiRpC6LmVLCetPPFNo"
    "Q66X7uxP0+9Zl8rjnrZnHII4V5AtpXV0UwAbxtKy37HKuWpuoF7Hcvab0FGJhs6U14TSDzsIHlnERZlISf3xN/Q9TmrLcSM97Oln"
    "o7MnUd5J6kl62NOPUsPfYe+Xtlb7lR7arnupXznjTXHwmPK0Z5zhnlBKVikJTPmpHU7lu5UAJTRRJ/tIdDCi/6okSFdpRP/taymz"
    "MCZH9F/1JQN/xH6w+j/LSMKmlEQzC87Hp0cHx7uHkc6jVZlbfP/FSDr8JXT1WnNr3os48XkwWjCL03miSSOCe2GbB2SN5gwXnshM"
    "rz04tADpRUqKGtOMv9cLyHgb1nof6g1FgmdJ3gx1EbHfQn3ELDpOpqTgw+rMxnqBfMhCiWSENQgtmqcT6D2UT+J0Wl/c1Y1DKQWv"
    "wOdVXPKkA/oBjAv7arKFCZzvEtLKscrIWZ5tia7aAkIrW4tKWAZm5S2xCRyyWtpS/VupzjobqhnyZYZ2wdskMyEW2x7owFJtaiPz"
    "YOMHRp14ji4xYvM68NJbxnSwxFi3CG0XgA2lQn6dliVmB1Vp8VwaTEmGSE1L7EI61qY6NqeX2LQCDW0N5SFdDQ5Uu8mpV2EVDdCH"
    "UtqV7PogN9NFeS4Wn+v0Qup6lVKD3VbZQZCkZ234Vtfc5FKKeA7KyaUr80JQWbnAWD09KIeRnRxaVsTdTBn02hLodaThV/GcXVWg"
    "DlaOVF6NtrFsW5GUV1lSwlUWziBCLdl5jZxGRFMtsGGevk1Cq7hrVZyA17oqlppiMIj2st/MsBW+V4HhN33fgpyEtKNhsHdyfH66"
    "u3ce/Tw+hQyPmjik7LNhTbCk7Cgdmq5TrbHiFx3afKVWOIg/c2hxcWrNVavAsC51rVD5h9xoYE5NqPBDRbjVweS8Zeiq+C1Zd4aB"
    "I44z5KSI2vCfdUlVsfsM1cPK0ZZgzxHKIeu6FMeKSqyhWGiVQ1PP1BpLui9trKjIamNZDxxaVENzbsJ6MayJqhFGgaElZPVOMmea"
    "Kdw+cmsmdnn2bEH7rayZaiAJTU/4AFZSw+Zps3WumrBWOsl5oj9f86XhnEInWDpJ4AT5R4lO1HkeT0sCMoQo30CuF9IigvfyDCyO"
    "K9zF/5ydHO/jhE8ruLCkrLQUsLhIgut4PsuL68Tqw6qZkpyxhjzs0n/vxee5AvCyVdUj4/mKhte+aqi0RjppVuMVgbGH2mmWZlcm"
    "dqXZ+onYaR476gtfbU66d1+zysgD1ExMa7j+1CZIbCb3z/nkfCAnR6T2rcP4bTRzWL614BSJoPJ5OrldAUDlQwd0apu2oCEBJZ8T"
    "/t0ePOnjhuBIvvrODxyACysG/vatUj/8E3YuJO8WaP6lsyf6Xu1Jeqit8FtvPwVrrvYsP7V7AJTvVvIA2EIGQPtnqPh+1DKQgJ9B"
    "LXz1HFuGr56/UYDjT1v76ltDR05Skt4AESX+LXxtilHKZjHzspFeqKzFTmc2E4UTSOYcGS0tTYL8Uz5h2SZg9Q6Y8ThP1xqQd2TF"
    "K7aaUrTIq+7CidFAIxfP9V9mJqBcbhUDc2zpEEuGTWL1DqlF0YU0JyS4sqA3upwGLM1MxS1ra5YAUmytLWlPkJzFYGu161kVM/51"
    "Y7CJZMwyw2r4o15zWjLe1tPoSS2l0meqpAlfyk9WtphaUNpoMlVYk0KtErxS5Aym6vgmTuECE9fowxUcQRZoeckUZ5Te2pY3Wdce"
    "um/1Ph4LnYfpy9+Qpon4Q0370A1XunA9NER6HRJF4B2q0rWOClVzh87JT8ZKcMmJmyglAcswn2Grnc1aR49lNo6BGqf8OzQPm3VM"
    "fK2sdrLsNrSIc6va4gwnyScVWKjEMPgbzJQABpod+wHscCIHvCgFzm7IWcsprFA2QRHCiDvNZY0zCyg8HvuPeinwHqwlzbcOWXxY"
    "083pttYSjevcw9w8bkmyRWm88N3ezCVdurwXK1fDpU6hLdfeUW87L9Xm3ypcqub+KHYBtIyXup9oVfNGqqNvazhYw2VWaWjnXX5r"
    "vOT6EZJrWaVkBRjJwzttGS/79nqJ9sJFIiUiiKsAHZAfqUrH5veYNTqeRk6+IjATRPKenn534UMogrLAAd9JN7j9PqQeO1cQlf1j"
    "TZ0zUbKyBmqhAHfAqypT1e3uOszX+LDlA80YB9rXZW75eGN5Vt21NMpU6qlu764REaRIO8D8i6ndMN+QKQE+a1h2fZSmVZdQKSD7"
    "wUKm1ooZ9iQ9liPUlfyc7lUm52NbC4+kdLWuMdRY4jFdvcjx7wYKzMw6PrU+atIZGHl8Eh5waQubxwclOgwgvpPRJ18e3synIAi2"
    "zypz9VjaVtMKPedRc00gbMhkBHmh6D1pxrztOaK6inrFf+5ZOI/IFMXIz9pKV3s0jcz6jaFN6LqO9StNUlc1COsXSmopWTJ39C9n"
    "mVLEZ2d7XUq17G2daVsZBedjeBWVU7jdxhLcm20LH4uvrLTUBn9yG+2odcmXFcuQYlzXlCKtPQi16dUHPcvzax35TJmm9L2vAGh8"
    "KYtw3De4LsPldMFxUUsXm7Ku+wVE+tvWPa3fjydCljHRIc90ptmnVfY51POgaYMbhujaNGkayxxqOdPWCgf19Zu0CRs1+OnQlp5t"
    "RQO3llz/04o0ldIktzBD04+ICRryKT+AbVvKGy2Zt8VTzayycpppsw6Vw7qtJZy2pIui5S9IHhRiFLaUkdhgRhRWbYiN3JgNRZQ0"
    "8bY91QzhSGfOolnUp1JUi5wwmTWWn6kBMFrnegyP9tqRV6HVZDHmlBHtKUDyGrCUl5sAqsqbQHpUFuVHcRNYSqHdwvjMv3FanUUL"
    "tYGU9Gvn6R9sENntz1I2b8n+LD/dnP35ozEKE7J/5EE+/4YKka/q01LvIMvtp3WoC2beDmJPXJdamy9xt7qeu/YV3YZruj4rZbmu"
    "63k5d6V18r5Zu4HbtZvSMbkwNuTijKE2UUF36ChQ8Xiiu4QcPHQWr9AlsGFtKQtVBBvWlLVoo9N5aPXyeTp0l71QzkOKI/XgXFnh"
    "0x0Sn1auNDY9Xqpr/PPB/vh4b2yt2aUGfGCGYand1bX7ZJSSXnr+ZLWVUufLXWqrfVoHni5POKTsKXEfQId9NOFPDxwpprsEWa92"
    "vQVbmeBazHtT3B5aEGdFXk3gD0yX2MtduWI1vNU4TrC7xHRmWFwVmlGtJ/GezzFADx4D5LoYJTuD5S7Z00ekO2kuW5fjoHV6Kltm"
    "pUa5bh5XOPy7nVrToNKoYQ61xWbRAmHX5/6YF5vd2WRchD4/p/7aRglstRjy0MvsIl9mRgzM3Bo6oIGu8QqgFemBLeLH6MDP02T7"
    "cvPJfVxYWsFNJUs5w8AUfO7V+9Ta4UOPyaZIY4vvh3/ZEMdruIH4h7VBso/IxSP49jCwMHj7hYb8ptTKLRtiP6spLhdJtUr/uNi4"
    "VuwTV6LWnk1xBomiffk6S3F0qetaPaKhlrZXHW1HDW2jfjZlz3r5bJ/S2fg6sWWa7qxK5Jid5PM5wZalePYrivD1amXb6mRbIFUb"
    "7GDHi9mKTHS7by1m3mIMpTBLmsl4YN9tsGZiRSqzKOK3jejp/TAlKz1q8NpVKZaXEafuMxXvbQrZ753sHiKhYNw92v2lS+mw1w+2"
    "e0TmeI9noEXpypLAq+3XtnQPKQIBpHQBKLa60VlyGQ0vKn8sC4+0hx9ED3XKlFovbhZSsDkpwIDxVRJPg3yGy6VvYbJQ5VW8MGhE"
    "MvQqw9H9Lg/GIDAyU+PROM1FZNAu7qhPQTC2LnksV52ydkIqlEukTUuU61zQUrQgDMNzuDlSJvE10iXncxzyVqKDEJc8oqty9r+H"
    "iPrB+DQNEEKyMibkBoXwdOBuCtQ0mueTN1E8IQokAzBHak0sw2dUk/aEMM/mt3T5/jx+fnAcHBwdjfcPds/HQX6D5GUAHcOBZC4E"
    "iIDTUX+CZQqKLuf5RTynQQ5gQ02zpVLYawWesCiWGeR3viry5eWVxgg8DiwDHS8gNSeeI0skgCTgWTqvII90QKYAfSQFmhdm29UV"
    "IlE2lMAGc3tbuO/3Otit3OFsFy4QQCDzYmARTYPGjoRRPMR1XLxRs3+QaPkJEuVxShYLVFsaVLIQ0qKK7N7Jy+Pz7peI7R0dHHeZ"
    "tRt+RTyR/6opWKFau50Wt6aqGQ8A+CH4Mdg93pdCCUaBUZFWnUTfNtWmArUMSyyWYlsSJa7TLL1eXktvd173WNaSHSVTiSU7yXX8"
    "Tvv6qfj6adPXWDNXV3IUbJspOfArpPJsg17EANYyTTBIVksoTPeBFqAh9rT94gRhvG5bLgdcnaVuzGUzwikI5MUOvgp2jMZ0nl+M"
    "6gU7zz1on/hlvEC8rsIwh/XFzJyiIuJ210zWV8VoSRIkZ6LG5qzcTaLVSJz62odU4pOrY60p6Nu1XTw3pBfhf3VnFcICevXeNLiQ"
    "ycLdd4vsi1sI+FArlxBMVFpzSuCLaxKg8bcGGtGXxjMbdBS9AJtVtqbORHLM0BSf/HeNr90ZGqSDwMjxqtIWYaePg7jWPbFXozyC"
    "gc+ktwrpUZ+yvGw4Y4dyznYsV0qoSBKVYD2ZwhAkMPJ0fI7+wcFo472T4/2zOlLvAFVzj/skXsQXKRIN04SW8mAOu6v8GtHai5iV"
    "OmTPsxieK/X+uP/TSVCiVOlIqRQNE0ISqzxmXxmJHHgGLbYLGQjjRSo13H1xYGvDwEItJPcl3KecdCUJB3zcU1I4FNaYy1hDeC6t"
    "dAjXeclyQVtYrVcOI+QQp/cNHaa4ITXThn335wnaAzWvczncTuJQNPZgJcoKIUIOdnxUpr8DKpA8Gr3YfT6Ozg7+NpbazZIYrR5G"
    "gMZMpssC9pvQrKCNeeeet2MFK7ivwd6cFakpEozXqIxnCZWA7R9wW+olzsDCQ+BZ5I/9K0m35FpYvqwu8nf29oysMTlMI5neLK1v"
    "kgtokSmT1dJEErqd0JQ05QJ88WYruv2NrV9OrpLrOEIYSmfo9y7evNUScaxXF7dID+prO7kfiNqeSCs7zEG/vgJPE/o6WMSTN4gY"
    "pgHpNqBdYFfdlFZmxXbCG6RkU2u0UO6AxCGNOAjy14u8qObpxYA9LAeAt7IbXqGFScpoMk8HQrXR5lOGvcE/8jRDyLgSBzbnDWd7"
    "P42PdqNnB4fj492jcUdYhi7yKXAnNugAb3M8C6rJKFHipFywkooYOiAtWVj4yywFS6WUYtg38bDXZcOQ41w4+xg22DJApCXP6iuk"
    "czWinBca0YuOAgH0mqFqDQhBE88DIqY1C9/rS7V/8Hx8dh7tHj4/OT04/+nobhjKzdFxfAXEUl7FT7/9jizC4Cp5R+MFetQ6qJwj"
    "0Kgf8DlCQ/vm6DoPNHkD8NnTRH2wusEZmTYYNPgZextkyWVeEaeHIP6IwxIRu4Bjf8pTwB90/j+0kUxX"
)


def _digest(label: str) -> str:
    return canonical_digest({"fixture": label})


def _store(tmp_path, *, profile="default", runtime="runtime-a"):
    return ExecutionContractStore(
        database_path=tmp_path / profile / "execution_contract.sqlite3",
        profile_name=profile,
        runtime_instance_id=runtime,
    )


def _bound_execution(store: ExecutionContractStore, *, suffix="one"):
    return store.create_execution(
        lifecycle="queued",
        source_run_id=f"run-{suffix}",
        work_ref=f"work:{suffix}",
        proposal_ref=f"proposal:{suffix}",
        effect_id=f"effect:{suffix}",
        now=NOW,
    )


def _resolved_decision(store: ExecutionContractStore, execution: dict, *, suffix="one"):
    decision = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest(f"request-{suffix}"),
        candidate_digest=_digest(f"candidate-{suffix}"),
        policy_digest=_digest(f"policy-{suffix}"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    return store.resolve_decision(
        decision["decision_id"],
        choice="once",
        resolution_evidence_digest=_digest(f"resolution-{suffix}"),
        now=NOW + timedelta(seconds=1),
    )


def _record_evidence(
    store: ExecutionContractStore,
    execution: dict,
    *,
    outcome="succeeded",
    decision_id=None,
    suffix="one",
    reconciliation_ref=None,
):
    return store.record_effect_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome=outcome,
        subject_digest=_digest(f"subject-{suffix}"),
        evidence_digest=_digest(f"evidence-{suffix}"),
        result_digest=_digest(f"result-{suffix}"),
        decision_id=decision_id,
        reconciliation_ref=reconciliation_ref,
        now=NOW + timedelta(seconds=2),
    )


def test_absent_read_is_side_effect_free_then_create_reopen_and_migrate(tmp_path):
    store = _store(tmp_path)
    assert not store.database_path.exists()
    assert not store.profile_anchor_path.exists()

    empty = store.list_executions()

    assert empty["items"] == []
    assert empty["page"] == {
        "cursor": 0,
        "high_water": 0,
        "snapshot_high_water": 0,
        "minimum_available": 0,
        "has_more": False,
        "completeness": "complete",
    }
    assert not store.database_path.exists()
    assert not store.profile_anchor_path.exists()

    execution = store.create_execution(
        lifecycle="accepted",
        source_run_id="run-create",
        work_ref="work:create",
        proposal_ref="proposal:create",
        now=NOW,
    )

    assert store.database_path.exists()
    assert store.profile_anchor_path.exists()
    assert os.stat(store.database_path).st_mode & 0o077 == 0
    assert os.stat(store.profile_anchor_path).st_mode & 0o777 == 0o600
    anchor_payload = json.loads(store.profile_anchor_path.read_text(encoding="utf-8"))
    assert anchor_payload["version"] == 1
    assert len(anchor_payload["instance_id"]) == 64
    assert str(store.profile_home) not in json.dumps(store.authority.public())
    assert hashlib.sha256(str(store.profile_home).encode()).hexdigest() not in json.dumps(
        store.authority.public()
    )
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    reopened = _store(tmp_path)
    assert reopened.get_execution(execution["execution_id"])["revision"] == 1
    assert reopened.get_execution(execution["execution_id"])["freshness"] == "live"


def test_schema_v1_migrates_private_action_submissions_without_changing_reads(
    tmp_path,
):
    store = _store(tmp_path)
    execution = store.create_execution(
        source_run_id="run-before-action-schema",
        now=NOW,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE action_submissions")
        connection.execute("PRAGMA user_version=1")

    reopened = _store(tmp_path)
    reopened.initialize()

    with sqlite3.connect(reopened.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='action_submissions'"
        ).fetchone() == ("action_submissions",)
    assert reopened.get_execution(execution["execution_id"])["source_run_id"] == (
        "run-before-action-schema"
    )


def test_schema_v1_migration_recovers_complete_table_and_rolls_back_partial_table(
    tmp_path,
):
    recoverable = _store(tmp_path, profile="recoverable")
    recoverable.initialize()
    with sqlite3.connect(recoverable.database_path) as connection:
        connection.execute("PRAGMA user_version=1")

    _store(tmp_path, profile="recoverable").initialize()
    with sqlite3.connect(recoverable.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2

    partial = _store(tmp_path, profile="partial")
    partial.initialize()
    with sqlite3.connect(partial.database_path) as connection:
        connection.execute("DROP TABLE action_submissions")
        connection.execute(
            "CREATE TABLE action_submissions (idempotency_key_hash TEXT PRIMARY KEY)"
        )
        connection.execute("PRAGMA user_version=1")

    with pytest.raises(ContractDataError, match="schema is incomplete"):
        _store(tmp_path, profile="partial").initialize()
    with sqlite3.connect(partial.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(action_submissions)"
            ).fetchall()
        ] == ["idempotency_key_hash"]


@pytest.mark.parametrize(
    "defect",
    ["primary_key", "unique", "foreign_key", "not_null", "type", "order", "check"],
)
def test_schema_v1_migration_rejects_exact_names_with_wrong_constraints(
    tmp_path,
    defect,
):
    store = _store(tmp_path, profile=defect)
    store.initialize()
    schema_sql = store._action_submissions_schema_sql()
    mutations = {
        "primary_key": (
            "idempotency_key_hash TEXT PRIMARY KEY",
            "idempotency_key_hash TEXT NOT NULL",
        ),
        "unique": ("run_id TEXT NOT NULL UNIQUE", "run_id TEXT NOT NULL"),
        "foreign_key": (
            "REFERENCES executions(execution_id)",
            "REFERENCES executions(source_run_id)",
        ),
        "not_null": ("profile_id TEXT NOT NULL", "profile_id TEXT"),
        "type": ("profile_id TEXT NOT NULL", "profile_id BLOB NOT NULL"),
        "order": (
            "profile_id TEXT NOT NULL,\n                authority_id TEXT NOT NULL,",
            "authority_id TEXT NOT NULL,\n                profile_id TEXT NOT NULL,",
        ),
        "check": (
            "length(idempotency_key_hash) = 64",
            "length(idempotency_key_hash) = 63",
        ),
    }
    old, new = mutations[defect]
    malformed_sql = schema_sql.replace(old, new, 1)
    assert malformed_sql != schema_sql

    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TABLE action_submissions")
        connection.execute(malformed_sql)
        connection.execute("PRAGMA user_version=1")
        assert {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(action_submissions)"
            ).fetchall()
        } == {
            "idempotency_key_hash",
            "profile_id",
            "authority_id",
            "request_digest",
            "run_id",
            "execution_id",
            "created_at",
        }

    with pytest.raises(ContractDataError, match="schema is incomplete"):
        _store(tmp_path, profile=defect).initialize()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_v1_binary_rollback_target_is_explicit_existing_and_identity_bound(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    store.initialize()
    expected_profile_id = store.authority.profile_id

    target = store.describe_v1_binary_rollback_target(
        expected_profile_id=expected_profile_id
    )
    assert target == {
        "profile_home": str(store.profile_home.resolve()),
        "database_path": str(store.database_path.resolve()),
        "profile_id": expected_profile_id,
        "schema_version": 2,
    }

    absent_path = tmp_path / "absent" / "execution_contract.sqlite3"
    absent = ExecutionContractStore(
        database_path=absent_path,
        profile_name="absent",
    )
    with pytest.raises(ContractNotFoundError, match="ledger is absent"):
        absent.describe_v1_binary_rollback_target(
            expected_profile_id=expected_profile_id
        )
    absent_backup = tmp_path / "absent-backup.sqlite3"
    with pytest.raises(ContractNotFoundError, match="ledger is absent"):
        absent.prepare_v1_binary_rollback(
            backup_path=absent_backup,
            expected_profile_id=expected_profile_id,
        )
    assert not absent_path.exists()
    assert not absent.profile_anchor_path.exists()
    assert not absent_backup.exists()

    with pytest.raises(ContractForbiddenError, match="expected profile identity"):
        store.describe_v1_binary_rollback_target(
            expected_profile_id="pro_wrong_expected_profile"
        )
    mismatched_backup = tmp_path / "mismatched-backup.sqlite3"
    with pytest.raises(ContractForbiddenError, match="expected profile identity"):
        store.prepare_v1_binary_rollback(
            backup_path=mismatched_backup,
            expected_profile_id="pro_wrong_expected_profile",
        )
    assert not mismatched_backup.exists()

    both_explicit = ExecutionContractStore(
        profile_home=store.profile_home,
        database_path=store.database_path,
        profile_name="default",
    )
    with pytest.raises(ContractValidationError, match="exactly one explicit"):
        both_explicit.describe_v1_binary_rollback_target(
            expected_profile_id=expected_profile_id
        )

    mismatched_path = tmp_path / "mismatched-path"
    mismatched_path.mkdir()
    linked_ledger = mismatched_path / "execution_contract.sqlite3"
    linked_ledger.symlink_to(store.database_path)
    path_mismatch = ExecutionContractStore(
        database_path=linked_ledger,
        profile_name="default",
    )
    with pytest.raises(ContractForbiddenError, match="does not belong"):
        path_mismatch.describe_v1_binary_rollback_target(
            expected_profile_id=expected_profile_id
        )

    monkeypatch.setenv("HERMES_HOME", str(store.profile_home))
    implicit = ExecutionContractStore(profile_name="default")
    with pytest.raises(ContractValidationError, match="exactly one explicit"):
        implicit.describe_v1_binary_rollback_target(
            expected_profile_id=expected_profile_id
        )


def test_exact_prior_v1_binary_subprocess_rollback_restore_and_current_reopen(
    tmp_path,
):
    store = _store(tmp_path)
    request_digest = canonical_digest({"input": "rollback-compatible action"})
    submission = store.create_action_submission(
        idempotency_key="rollback-compatible-key",
        request_digest=request_digest,
        run_id="run_rollback_compatible",
        now=NOW,
    )
    backup_path = tmp_path / "execution-contract-v2-backup.sqlite3"
    expected_profile_id = store.authority.profile_id

    store.prepare_v1_binary_rollback(
        backup_path=backup_path,
        expected_profile_id=expected_profile_id,
    )

    assert backup_path.exists()
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    with sqlite3.connect(backup_path) as backup_connection:
        assert backup_connection.execute("PRAGMA user_version").fetchone()[0] == 2

    repo_root = Path(__file__).parents[2]
    prior_source = zlib.decompress(base64.b64decode(PRIOR_V1_SOURCE_B64))
    assert hashlib.sha256(prior_source).hexdigest() == PRIOR_V1_SOURCE_SHA256
    assert PRIOR_V1_BASE_SHA == "28f24e44b890b817ca23a31e678e0e34f1781d1e"
    prior_source_path = tmp_path / "prior_execution_contract.py"
    prior_source_path.write_bytes(prior_source)
    prior_probe = """
import importlib.util
import sys

source_path, database_path, profile_home, execution_id = sys.argv[1:]
spec = importlib.util.spec_from_file_location("prior_execution_contract", source_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
store = module.ExecutionContractStore(
    database_path=module.Path(database_path),
    profile_home=module.Path(profile_home),
    profile_name="default",
    runtime_instance_id="prior-v1-subprocess",
)
store.initialize()
execution = store.get_execution(execution_id)
assert execution["source_run_id"] == "run_rollback_compatible"
print(execution["execution_id"])
"""
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            prior_probe,
            str(prior_source_path),
            str(store.database_path),
            str(store.profile_home),
            submission["execution"]["execution_id"],
        ],
        cwd=repo_root,
        env={
            "LANG": "C.UTF-8",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(repo_root),
            "TZ": "UTC",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == submission["execution"]["execution_id"]

    # A failed prior-binary can be stopped and the exact v2 snapshot restored.
    store.restore_v2_rollback_backup(
        backup_path=backup_path,
        expected_profile_id=expected_profile_id,
    )
    with sqlite3.connect(store.database_path) as restored_connection:
        assert restored_connection.execute("PRAGMA user_version").fetchone()[0] == 2
    replay = store.lookup_action_submission(
        idempotency_key="rollback-compatible-key",
        request_digest=request_digest,
    )
    assert replay == {**submission, "replayed": True}

    # A successful prior-binary window can instead be upgraded in place; the
    # retained v2 table makes the migration idempotent and preserves replay.
    second_backup = tmp_path / "execution-contract-v2-backup-second.sqlite3"
    store.prepare_v1_binary_rollback(
        backup_path=second_backup,
        expected_profile_id=expected_profile_id,
    )
    _store(tmp_path).initialize()
    assert store.lookup_action_submission(
        idempotency_key="rollback-compatible-key",
        request_digest=request_digest,
    ) == {**submission, "replayed": True}


def test_action_submission_exact_replay_is_durable_and_changed_request_conflicts(
    tmp_path,
):
    store = _store(tmp_path)
    request_digest = canonical_digest(
        {"input": "synthetic private action", "effect_id": "effect:action"}
    )
    first = store.create_action_submission(
        idempotency_key="retry-action-0001",
        request_digest=request_digest,
        run_id="run_action_0001",
        work_ref="work:action",
        proposal_ref="proposal:action",
        effect_id="effect:action",
        now=NOW,
    )
    replay = _store(tmp_path).create_action_submission(
        idempotency_key="retry-action-0001",
        request_digest=request_digest,
        run_id="run_unused_replay_candidate",
        work_ref="work:action",
        proposal_ref="proposal:action",
        effect_id="effect:action",
        now=NOW + timedelta(seconds=1),
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["run_id"] == first["run_id"]
    assert replay["execution"] == first["execution"]
    assert store.list_executions()["page"]["high_water"] == 1
    assert store.list_events()["page"]["high_water"] == 1

    with pytest.raises(ContractConflictError, match="different request"):
        store.create_action_submission(
            idempotency_key="retry-action-0001",
            request_digest=canonical_digest({"input": "changed action"}),
            run_id="run_changed_action",
            work_ref="work:action",
            proposal_ref="proposal:action",
            effect_id="effect:action",
            now=NOW + timedelta(seconds=2),
        )


def test_action_submission_lookup_is_read_only_and_transaction_lock_is_retryable(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    request_digest = canonical_digest({"input": "synthetic locked action"})
    store.initialize()

    assert store.lookup_action_submission(
        idempotency_key="retry-locked-action",
        request_digest=request_digest,
    ) is None
    assert store.list_executions()["items"] == []

    writer = store._connect_write()
    writer.execute("PRAGMA busy_timeout=1")
    blocker = sqlite3.connect(store.database_path, timeout=0.001)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(store, "initialize", lambda: None)
            patcher.setattr(store, "_connect_write", lambda: writer)
            with pytest.raises(ContractRateLimitedError, match="ledger is busy"):
                store.create_action_submission(
                    idempotency_key="retry-locked-action",
                    request_digest=request_digest,
                    run_id="run_locked_action",
                    now=NOW,
                )
    finally:
        blocker.rollback()
        blocker.close()

    retry = store.create_action_submission(
        idempotency_key="retry-locked-action",
        request_digest=request_digest,
        run_id="run_locked_action",
        now=NOW,
    )
    replay = store.lookup_action_submission(
        idempotency_key="retry-locked-action",
        request_digest=request_digest,
    )
    assert retry["replayed"] is False
    assert replay == {**retry, "replayed": True}


def test_action_submission_persists_only_hashes_and_rolls_back_atomically(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    raw_key = "private-retry-key-that-must-not-be-stored"
    private_input = "private action payload that must not be stored"
    store.create_action_submission(
        idempotency_key=raw_key,
        request_digest=canonical_digest({"input": private_input}),
        run_id="run_private_hash_only",
        now=NOW,
    )
    database_body = store.database_path.read_bytes()
    assert raw_key.encode() not in database_body
    assert private_input.encode() not in database_body

    original_append = store._append_event_in_txn

    def fail_append(*args, **kwargs):
        raise RuntimeError("synthetic append failure")

    monkeypatch.setattr(store, "_append_event_in_txn", fail_append)
    with pytest.raises(RuntimeError, match="synthetic append failure"):
        store.create_action_submission(
            idempotency_key="rollback-key",
            request_digest=canonical_digest({"input": "rollback"}),
            run_id="run_action_rollback",
            now=NOW,
        )
    monkeypatch.setattr(store, "_append_event_in_txn", original_append)

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM action_submissions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM executions WHERE source_run_id='run_action_rollback'"
        ).fetchone()[0] == 0


def test_packaged_schema_and_synthetic_fixtures_are_closed(tmp_path):
    schema = contract_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    fixture_defs = {
        "capabilities.json": ("capabilities", None),
        "execution-list.json": ("executionList", "execution"),
        "decision-list.json": ("decisionList", "decision"),
        "event-list.json": ("eventList", "event"),
        "receipt-list.json": ("receiptList", "receipt"),
    }
    for filename, (definition_name, item_definition_name) in fixture_defs.items():
        payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        definition = schema["$defs"][definition_name]
        assert definition["additionalProperties"] is False
        assert set(payload) == set(definition["required"])
        if item_definition_name:
            collection = definition_name.removesuffix("List").lower() + "s"
            item_definition = schema["$defs"][item_definition_name]
            assert item_definition["additionalProperties"] is False
            assert set(payload[collection][0]) == set(item_definition["required"])

    synthetic_home = tmp_path / "synthetic-profile"
    synthetic_store = ExecutionContractStore(
        profile_home=synthetic_home,
        profile_name="synthetic",
    )
    synthetic_store.initialize()
    capabilities = contract_capabilities(synthetic_home, "synthetic")
    fixture_capabilities = json.loads(
        (FIXTURES / "capabilities.json").read_text(encoding="utf-8")
    )
    assert capabilities["authority"]["profile_name"] == "synthetic"
    assert capabilities["authority"]["profile_id"].startswith(
        "hermes-profile-instance:"
    )
    assert {
        key: value for key, value in capabilities.items() if key != "authority"
    } == {
        key: value for key, value in fixture_capabilities.items() if key != "authority"
    }
    assert schema["$defs"]["errorDetail"]["additionalProperties"] is False


def test_packaged_action_schema_binds_submission_read_and_release_identities():
    body, schema, digest = action_contract_schema_artifact()

    assert action_contract_schema() == schema
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(
        "hermes.execution.action.v1.schema.json"
    )
    assert digest == f"sha256:{hashlib.sha256(body).hexdigest()}"
    assert ACTION_CONTRACT_VERSION == "hermes.execution.action.v1"
    assert schema["$defs"]["submissionEnvelope"]["properties"][
        "contract_version"
    ] == {"const": ACTION_CONTRACT_VERSION}
    assert schema["$defs"]["statusBinding"]["properties"][
        "contract_version"
    ] == {"const": CONTRACT_VERSION}
    assert schema["$defs"]["terminalReceiptBinding"]["properties"][
        "contract_version"
    ] == {"const": CONTRACT_VERSION}
    assert schema["$defs"]["releaseReceipt"]["properties"][
        "receipt_version"
    ] == {"const": "hermes.execution.action.release-receipt.v1"}


def test_unknown_store_version_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA user_version=99")

    with pytest.raises(UnsupportedContractVersionError):
        store.initialize()
    with pytest.raises(UnsupportedContractVersionError):
        store.list_executions()


@pytest.mark.parametrize(
    ("outcome", "lifecycle"),
    [
        ("succeeded", "terminal_succeeded"),
        ("failed", "terminal_failed"),
        ("cancelled", "terminal_cancelled"),
        ("partial", "terminal_partial"),
        ("ambiguous", "terminal_ambiguous"),
    ],
)
def test_all_evidence_backed_receipt_outcomes_publish_atomically(
    tmp_path,
    outcome,
    lifecycle,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=outcome)
    store.transition_execution(execution["execution_id"], "running", now=NOW)
    evidence = _record_evidence(store, execution, outcome=outcome, suffix=outcome)

    terminal = store.transition_execution(
        execution["execution_id"],
        lifecycle,
        now=NOW + timedelta(seconds=3),
    )

    assert evidence["outcome"] == outcome
    assert terminal["lifecycle"] == lifecycle
    assert terminal["receipt_state"] == "published"
    receipt = store.get_receipt(terminal["receipt_id"])
    assert receipt["execution_id"] == execution["execution_id"]
    assert receipt["effect_id"] == execution["effect_id"]
    assert receipt["outcome"] == outcome
    assert receipt["revision"] == 1
    events = store.list_events(execution_id=execution["execution_id"])["items"]
    assert events[-2]["event_type"] == "execution.transitioned"
    assert events[-1]["event_type"] == "receipt.published"
    assert events[-1]["receipt_id"] == receipt["receipt_id"]


def test_nonterminal_lifecycle_progression_and_initial_state_are_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="accepted", now=NOW)
    queued = store.transition_execution(
        execution["execution_id"],
        "queued",
        now=NOW + timedelta(seconds=1),
    )
    running = store.transition_execution(
        execution["execution_id"],
        "running",
        now=NOW + timedelta(seconds=2),
    )
    cancelling = store.transition_execution(
        execution["execution_id"],
        "cancellation_requested",
        now=NOW + timedelta(seconds=3),
    )
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_cancelled",
        now=NOW + timedelta(seconds=4),
    )
    assert [
        queued["lifecycle"],
        running["lifecycle"],
        cancelling["lifecycle"],
        terminal["lifecycle"],
    ] == ["queued", "running", "cancellation_requested", "terminal_cancelled"]
    with pytest.raises(ContractValidationError, match="initial lifecycle"):
        store.create_execution(lifecycle="awaiting_decision", now=NOW)


def test_unproven_effect_is_terminal_ambiguous_without_receipt(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    store.transition_execution(execution["execution_id"], "running", now=NOW)

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        reason_code="generic_chat_completed",
        now=NOW + timedelta(seconds=1),
    )

    assert terminal["lifecycle"] == "terminal_ambiguous"
    assert terminal["receipt_state"] == "unproven"
    assert terminal["receipt_id"] is None
    assert store.list_receipts()["items"] == []


def test_execution_without_external_effect_can_terminate_without_receipt(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(
        lifecycle="running",
        source_run_id="run-chat-only",
        work_ref="work:chat",
        proposal_ref="proposal:chat",
        now=NOW,
    )

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        now=NOW + timedelta(seconds=1),
    )

    assert terminal["lifecycle"] == "terminal_succeeded"
    assert terminal["receipt_state"] == "not_applicable"
    assert terminal["receipt_id"] is None


def test_pending_resolved_expired_and_superseded_decisions(tmp_path):
    store = _store(tmp_path)

    resolved_execution = _bound_execution(store, suffix="resolved")
    resolved = _resolved_decision(store, resolved_execution, suffix="resolved")
    assert resolved["state"] == "resolved"
    assert resolved["choice"] == "once"
    assert resolved["resolution_evidence_digest"] == _digest("resolution-resolved")

    expiring_execution = _bound_execution(store, suffix="expired")
    expiring = store.create_decision(
        execution_id=expiring_execution["execution_id"],
        effect_id=expiring_execution["effect_id"],
        proposal_ref=expiring_execution["proposal_ref"],
        request_digest=_digest("request-expired"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(seconds=1),
        now=NOW,
    )
    assert store.expire_decisions(now=NOW + timedelta(seconds=2)) == [
        expiring["decision_id"]
    ]
    assert store.get_decision(expiring["decision_id"])["state"] == "expired"
    assert store.get_execution(expiring_execution["execution_id"])["lifecycle"] == (
        "terminal_ambiguous"
    )

    superseded_execution = _bound_execution(store, suffix="superseded")
    superseded = store.create_decision(
        execution_id=superseded_execution["execution_id"],
        effect_id=superseded_execution["effect_id"],
        proposal_ref=superseded_execution["proposal_ref"],
        request_digest=_digest("request-superseded"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    superseded = store.supersede_decision(
        superseded["decision_id"],
        reason_code="proposal_replaced",
        now=NOW + timedelta(seconds=1),
    )
    assert superseded["state"] == "superseded"
    assert store.list_decisions(state="pending")["items"] == []


def test_one_pending_decision_and_terminalization_closes_it(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    decision = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest("request-first"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    with pytest.raises(ContractConflictError, match="already has a pending"):
        store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest("request-second"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=NOW + timedelta(seconds=1),
    )
    assert terminal["lifecycle"] == "terminal_ambiguous"
    assert store.get_decision(decision["decision_id"])["state"] == "superseded"
    assert store.expire_decisions(now=NOW + timedelta(minutes=10)) == []


def test_decision_and_effect_bindings_fail_closed(tmp_path):
    store = _store(tmp_path)
    first = _bound_execution(store, suffix="first")
    second = _bound_execution(store, suffix="second")

    with pytest.raises(ContractConflictError, match="effect binding"):
        store.create_decision(
            execution_id=first["execution_id"],
            effect_id=second["effect_id"],
            proposal_ref=first["proposal_ref"],
            request_digest=_digest("wrong-effect"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    decision = _resolved_decision(store, first, suffix="first")
    with pytest.raises(ContractConflictError, match="exact decision"):
        _record_evidence(store, first, decision_id=None)
    with pytest.raises(ContractConflictError, match="not bound"):
        _record_evidence(
            store,
            first,
            decision_id="dec_"
            + store.authority.profile_key
            + "_"
            + "f" * 32,
        )
    with pytest.raises(ContractConflictError, match="effect evidence binding"):
        store.record_effect_evidence(
            execution_id=first["execution_id"],
            effect_id="effect:wrong",
            outcome="succeeded",
            subject_digest=_digest("subject"),
            evidence_digest=_digest("evidence"),
            result_digest=_digest("result"),
            decision_id=decision["decision_id"],
            now=NOW,
        )


def test_duplicate_evidence_is_idempotent_and_conflict_is_rejected(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    first = _record_evidence(store, execution)
    duplicate = _record_evidence(store, execution)
    assert duplicate == first

    with pytest.raises(ContractConflictError, match="conflicting effect evidence"):
        _record_evidence(store, execution, outcome="partial")


def test_optimistic_revision_and_out_of_order_lifecycle_fail_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)

    running = store.transition_execution(
        execution["execution_id"],
        "running",
        expected_revision=1,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ContractConflictError, match="revision"):
        store.transition_execution(
            execution["execution_id"],
            "cancellation_requested",
            expected_revision=1,
            now=NOW + timedelta(seconds=2),
        )
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        expected_revision=running["revision"],
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ContractConflictError, match="invalid execution transition"):
        store.transition_execution(
            terminal["execution_id"],
            "running",
            now=NOW + timedelta(seconds=3),
        )


def test_transaction_rolls_back_state_when_event_append_fails(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("synthetic crash boundary")

    monkeypatch.setattr(store, "_append_event_in_txn", fail_event)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        store.transition_execution(
            execution["execution_id"],
            "running",
            now=NOW + timedelta(seconds=1),
        )

    reopened = _store(tmp_path)
    current = reopened.get_execution(execution["execution_id"])
    assert current["lifecycle"] == "queued"
    assert current["revision"] == 1
    assert len(reopened.list_events()["items"]) == 1


def test_terminal_receipt_and_event_roll_back_together(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    store.transition_execution(execution["execution_id"], "running", now=NOW)
    _record_evidence(store, execution)
    original_append = store._append_event_in_txn

    def fail_terminal_event(*args, **kwargs):
        if kwargs.get("event_type") == "execution.transitioned":
            raise RuntimeError("synthetic crash after receipt insert")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(store, "_append_event_in_txn", fail_terminal_event)
    with pytest.raises(RuntimeError, match="after receipt insert"):
        store.transition_execution(
            execution["execution_id"],
            "terminal_succeeded",
            now=NOW + timedelta(seconds=3),
        )

    reopened = _store(tmp_path)
    current = reopened.get_execution(execution["execution_id"])
    assert current["lifecycle"] == "running"
    assert current["receipt_state"] == "pending_evidence"
    assert reopened.list_receipts()["items"] == []


def test_restart_marks_orphaned_nonterminal_execution_ambiguous(tmp_path):
    first_runtime = _store(tmp_path, runtime="runtime-a")
    execution = first_runtime.create_execution(lifecycle="running", now=NOW)

    second_runtime = _store(tmp_path, runtime="runtime-b")
    before = second_runtime.get_execution(execution["execution_id"])
    assert before["freshness"] == "stale"

    recovered = second_runtime.recover_orphaned_executions(
        recovery_ref="restart:test",
        now=NOW + timedelta(seconds=1),
    )

    assert recovered == [execution["execution_id"]]
    after = second_runtime.get_execution(execution["execution_id"])
    assert after["lifecycle"] == "terminal_ambiguous"
    assert after["freshness"] == "terminal"
    assert after["recovery_ref"] == "restart:test"


def test_concurrent_append_has_monotonic_unique_sequences_and_pagination(tmp_path):
    path = tmp_path / "shared" / "execution_contract.sqlite3"

    def create(index: int) -> str:
        store = ExecutionContractStore(
            database_path=path,
            profile_name="default",
            runtime_instance_id="runtime-a",
        )
        return store.create_execution(
            lifecycle="queued",
            source_run_id=f"run-{index}",
            now=NOW + timedelta(microseconds=index),
        )["execution_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create, range(24)))

    assert len(set(ids)) == 24
    store = ExecutionContractStore(
        database_path=path,
        profile_name="default",
        runtime_instance_id="runtime-a",
    )
    first = store.list_events(limit=7)
    second = store.list_events(after=first["page"]["cursor"], limit=200)
    sequences = [event["sequence"] for event in first["items"] + second["items"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences)) == 24
    assert first["page"]["has_more"] is True
    assert second["page"]["high_water"] == sequences[-1]
    assert second["page"]["has_more"] is False


def test_pruned_cursor_returns_explicit_gap(tmp_path):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    execution = store.create_execution(lifecycle="running", now=old)
    store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    assert store.prune_events(now=NOW) == 2

    with pytest.raises(ContractCursorGoneError) as exc:
        store.list_events(after=0)
    assert exc.value.minimum_available == 3
    assert exc.value.high_water == 2


def test_retention_does_not_prune_across_a_live_execution_gap(tmp_path):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    live = store.create_execution(lifecycle="running", now=old)
    terminal = store.create_execution(lifecycle="running", now=old)
    store.transition_execution(
        terminal["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )

    assert store.prune_events(now=NOW) == 0
    events = store.list_events(after=0)["items"]
    assert events[0]["execution_id"] == live["execution_id"]
    assert len(events) == 3


@pytest.mark.parametrize("missing_sequence", [1, 2, 4])
def test_prune_rejects_global_gap_without_mutating_rows_or_watermark(
    tmp_path,
    missing_sequence,
):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    executions = []
    for index in range(2):
        execution = store.create_execution(
            lifecycle="running",
            source_run_id=f"prune-gap-{index}",
            now=old + timedelta(microseconds=index),
        )
        executions.append(execution)
        store.transition_execution(
            execution["execution_id"],
            "terminal_failed",
            now=old + timedelta(seconds=1, microseconds=index),
        )

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM execution_events WHERE sequence=?",
            (missing_sequence,),
        )
        connection.commit()
        before_rows = connection.execute(
            "SELECT sequence, event_id FROM execution_events ORDER BY sequence"
        ).fetchall()
        before_watermark = connection.execute(
            "SELECT value FROM execution_contract_metadata "
            "WHERE key='events_pruned_through'"
        ).fetchone()[0]

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.prune_events(now=NOW)

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT sequence, event_id FROM execution_events ORDER BY sequence"
        ).fetchall() == before_rows
        assert connection.execute(
            "SELECT value FROM execution_contract_metadata "
            "WHERE key='events_pruned_through'"
        ).fetchone()[0] == before_watermark == "0"

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events()
    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events(execution_id=executions[0]["execution_id"])


@pytest.mark.parametrize("missing_sequence", [4, 5, 6])
def test_prune_rejects_gap_in_retained_suffix_before_deleting_terminal_prefix(
    tmp_path,
    missing_sequence,
):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    terminal = store.create_execution(
        lifecycle="running",
        source_run_id="prunable-terminal-prefix",
        now=old,
    )
    store.transition_execution(
        terminal["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    live = store.create_execution(
        lifecycle="running",
        source_run_id="live-prune-boundary",
        now=old + timedelta(seconds=2),
    )
    retained = store.create_execution(
        lifecycle="accepted",
        source_run_id="retained-terminal-suffix",
        now=NOW,
    )
    store.transition_execution(
        retained["execution_id"],
        "running",
        now=NOW + timedelta(seconds=1),
    )
    store.transition_execution(
        retained["execution_id"],
        "terminal_failed",
        now=NOW + timedelta(seconds=2),
    )

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT sequence FROM execution_events ORDER BY sequence"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,)]
        connection.execute(
            "DELETE FROM execution_events WHERE sequence=?",
            (missing_sequence,),
        )
        connection.commit()
        before_rows = connection.execute(
            "SELECT sequence, event_id, execution_id FROM execution_events "
            "ORDER BY sequence"
        ).fetchall()
        before_watermark = connection.execute(
            "SELECT value FROM execution_contract_metadata "
            "WHERE key='events_pruned_through'"
        ).fetchone()[0]

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.prune_events(now=NOW)

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT sequence, event_id, execution_id FROM execution_events "
            "ORDER BY sequence"
        ).fetchall() == before_rows
        assert connection.execute(
            "SELECT value FROM execution_contract_metadata "
            "WHERE key='events_pruned_through'"
        ).fetchone()[0] == before_watermark == "0"

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events()
    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events(execution_id=terminal["execution_id"])
    assert live["lifecycle"] == "running"


def test_prune_boundary_excludes_append_waiting_on_same_write_lock(tmp_path):
    seed = _store(tmp_path, runtime="prune-race")
    old = NOW - timedelta(days=40)
    execution = seed.create_execution(lifecycle="running", now=old)
    seed.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    pruner = _store(tmp_path, runtime="prune-race")
    writer = _store(tmp_path, runtime="prune-race")
    prune_locked = threading.Event()
    release_prune = threading.Event()
    writer_attempted = threading.Event()

    def hold_prune(operation, _execution_id):
        if operation == "prune":
            prune_locked.set()
            assert release_prune.wait(timeout=5)

    pruner._after_write_lock_acquired = hold_prune

    def append_event():
        writer_attempted.set()
        return writer.create_execution(
            lifecycle="queued",
            source_run_id="after-prune-boundary",
            now=NOW,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        prune_future = pool.submit(pruner.prune_events, now=NOW)
        assert prune_locked.wait(timeout=5)
        append_future = pool.submit(append_event)
        assert writer_attempted.wait(timeout=5)
        assert not append_future.done()
        release_prune.set()
        assert prune_future.result(timeout=10) == 2
        appended = append_future.result(timeout=10)

    page = seed.list_events(after=2)
    assert [event["execution_id"] for event in page["items"]] == [
        appended["execution_id"]
    ]
    assert page["page"]["pruned_through"] == 2
    assert page["page"]["snapshot_high_water"] == 3
    assert page["page"]["completeness"] == "complete"


def test_profile_crossing_and_malformed_identifiers_are_distinct(tmp_path):
    first = _store(tmp_path, profile="first")
    second = _store(tmp_path, profile="second")
    execution = first.create_execution(lifecycle="queued", now=NOW)
    second.initialize()

    with pytest.raises(ContractForbiddenError):
        second.get_execution(execution["execution_id"])
    with pytest.raises(ContractValidationError):
        first.get_execution("run_not-an-execution-id")


def test_concurrent_profile_initialization_creates_one_stable_anchor(tmp_path):
    profile_home = tmp_path / "concurrent-profile"

    def initialize(_index: int):
        store = ExecutionContractStore(
            profile_home=profile_home,
            runtime_instance_id="runtime-a",
        )
        store.initialize()
        return store.authority

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        authorities = list(pool.map(initialize, range(16)))

    assert len(set(authorities)) == 1
    anchor = profile_home / ".execution-contract-profile-instance.json"
    assert anchor.exists()
    assert stat.S_IMODE(anchor.stat().st_mode) == 0o600
    payload = json.loads(anchor.read_text(encoding="utf-8"))
    assert set(payload) == {"version", "instance_id"}


def test_initialize_retries_transient_journal_mode_lock(monkeypatch, tmp_path):
    import hermes_state

    original = hermes_state.apply_wal_with_fallback
    attempts = []

    def transient_lock(connection, **kwargs):
        attempts.append(connection)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(connection, **kwargs)

    monkeypatch.setattr(hermes_state, "apply_wal_with_fallback", transient_lock)
    store = ExecutionContractStore(profile_home=tmp_path / "retry-profile")

    store.initialize()

    assert len(attempts) == 2
    assert store.list_executions()["items"] == []


def test_full_profile_home_move_and_restore_preserve_authority(tmp_path):
    original_home = tmp_path / "profile-original"
    moved_home = tmp_path / "profile-moved"
    restored_home = tmp_path / "profile-restored"
    backup_home = tmp_path / "profile-backup"
    original = ExecutionContractStore(
        profile_home=original_home,
        runtime_instance_id="runtime-a",
    )
    execution = original.create_execution(lifecycle="queued", now=NOW)
    original_authority = original.authority

    original_home.rename(moved_home)
    moved = ExecutionContractStore(
        profile_home=moved_home,
        runtime_instance_id="runtime-a",
    )
    assert moved.get_execution(execution["execution_id"])["execution_id"] == (
        execution["execution_id"]
    )
    assert moved.authority.profile_id == original_authority.profile_id
    assert moved.authority.authority_id == original_authority.authority_id

    moved_home.rename(restored_home)
    restored = ExecutionContractStore(
        profile_home=restored_home,
        runtime_instance_id="runtime-a",
    )
    assert restored.authority.profile_id == original_authority.profile_id
    assert restored.get_execution(execution["execution_id"])["execution_id"] == (
        execution["execution_id"]
    )
    with sqlite3.connect(restored.database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copytree(restored_home, backup_home)
    backup = ExecutionContractStore(
        profile_home=backup_home,
        runtime_instance_id="runtime-a",
    )
    assert backup.authority.profile_id == original_authority.profile_id
    assert backup.get_execution(execution["execution_id"])["execution_id"] == (
        execution["execution_id"]
    )


def test_database_only_copy_fails_against_destination_anchor(tmp_path):
    source_home = tmp_path / "source-profile"
    destination_home = tmp_path / "destination-profile"
    source = ExecutionContractStore(profile_home=source_home)
    source.create_execution(lifecycle="queued", now=NOW)
    with sqlite3.connect(source.database_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    destination_identity = ExecutionContractStore(
        profile_home=destination_home,
        database_path=destination_home / "identity-seed.sqlite3",
    )
    destination_identity.initialize()
    shutil.copy2(source.database_path, destination_home / "execution_contract.sqlite3")
    copied = ExecutionContractStore(profile_home=destination_home)

    with pytest.raises(ContractDataError, match="authority metadata"):
        copied.list_executions()
    with pytest.raises(ContractDataError, match="authority metadata"):
        copied.initialize()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "unsafe-mode", "symlink"])
def test_missing_corrupt_or_unsafe_profile_anchor_fails_closed(tmp_path, damage):
    store = _store(tmp_path)
    store.create_execution(lifecycle="queued", now=NOW)
    anchor = store.profile_anchor_path
    if damage == "missing":
        anchor.unlink()
    elif damage == "corrupt":
        anchor.write_text('{"version":1,"instance_id":"bad"}\n', encoding="utf-8")
        anchor.chmod(0o600)
    elif damage == "unsafe-mode":
        anchor.chmod(0o644)
    else:
        anchor.unlink()
        anchor.symlink_to(store.database_path)

    reopened = _store(tmp_path)
    with pytest.raises(ContractDataError, match="profile instance anchor"):
        reopened.list_executions()
    with pytest.raises(ContractDataError, match="profile instance anchor"):
        reopened.initialize()


def test_malformed_persisted_state_and_cursor_ahead_fail_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE executions SET lifecycle='future_state' WHERE execution_id=?",
            (execution["execution_id"],),
        )
        connection.commit()

    with pytest.raises(ContractDataError, match="unknown state"):
        store.get_execution(execution["execution_id"])
    with pytest.raises(ContractConflictError, match="cursor"):
        store.list_events(after=999)


def test_closed_reference_and_digest_validation(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ContractValidationError):
        store.create_execution(lifecycle="queued", work_ref="bad\nref", now=NOW)
    with pytest.raises(ContractValidationError, match="exact work_ref"):
        store.create_execution(
            lifecycle="queued",
            effect_id="effect:unbound",
            now=NOW,
        )
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        store.create_execution(
            lifecycle="queued",
            now=datetime(2026, 8, 15, 12, 0),
        )
    execution = _bound_execution(store)
    with pytest.raises(ContractValidationError, match="SHA-256"):
        store.record_effect_evidence(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            outcome="succeeded",
            subject_digest="not-a-digest",
            evidence_digest=_digest("evidence"),
            result_digest=_digest("result"),
            now=NOW,
        )
    assert CONTRACT_VERSION == execution["contract_version"]


def test_restart_recovery_transactionally_supersedes_pending_decision(tmp_path):
    first_runtime = _store(tmp_path, runtime="runtime-a")
    execution = _bound_execution(first_runtime, suffix="restart-pending")
    decision = first_runtime.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest("restart-pending"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )

    second_runtime = _store(tmp_path, runtime="runtime-b")
    assert second_runtime.recover_orphaned_executions(
        recovery_ref="restart:pending",
        now=NOW + timedelta(seconds=1),
    ) == [execution["execution_id"]]

    assert second_runtime.get_execution(execution["execution_id"])["lifecycle"] == (
        "terminal_ambiguous"
    )
    assert second_runtime.get_decision(decision["decision_id"])["state"] == (
        "superseded"
    )
    assert second_runtime.list_decisions(state="pending")["items"] == []


def test_decision_request_racing_cancellation_never_leaves_pending(tmp_path):
    seed = _store(tmp_path, runtime="runtime-race")
    execution = _bound_execution(seed, suffix="cancel-race")
    seed.transition_execution(execution["execution_id"], "running", now=NOW)
    decision_store = _store(tmp_path, runtime="runtime-race")
    cancel_store = _store(tmp_path, runtime="runtime-race")
    barrier = threading.Barrier(2)

    def request_decision():
        barrier.wait()
        try:
            return decision_store.create_decision(
                execution_id=execution["execution_id"],
                effect_id=execution["effect_id"],
                proposal_ref=execution["proposal_ref"],
                request_digest=_digest("cancel-race"),
                allowed_choices=["once", "deny"],
                expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )
        except ContractConflictError:
            return None

    def request_cancel():
        barrier.wait()
        return cancel_store.transition_execution(
            execution["execution_id"],
            "cancellation_requested",
            now=NOW + timedelta(seconds=1),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        decision_future = pool.submit(request_decision)
        cancel_future = pool.submit(request_cancel)
        decision_result = decision_future.result()
        cancel_future.result()

    current = seed.get_execution(execution["execution_id"])
    assert current["lifecycle"] == "cancellation_requested"
    assert seed.list_decisions(state="pending")["items"] == []
    if decision_result is not None:
        assert seed.get_decision(decision_result["decision_id"])["state"] == (
            "superseded"
        )


@pytest.mark.parametrize("operation", ["resolve", "supersede"])
def test_decision_close_cannot_revive_terminal_execution(tmp_path, operation):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=f"terminal-{operation}")
    decision = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest(f"terminal-{operation}"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    terminal = "2026-08-15T12:00:01.000000Z"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE executions SET lifecycle='terminal_ambiguous', "
            "receipt_state='unproven', terminal_at=?, updated_at=? "
            "WHERE execution_id=?",
            (terminal, terminal, execution["execution_id"]),
        )
        connection.commit()

    with pytest.raises(ContractConflictError, match="terminal_ambiguous"):
        if operation == "resolve":
            store.resolve_decision(
                decision["decision_id"],
                choice="once",
                resolution_evidence_digest=_digest("terminal-resolution"),
                now=NOW + timedelta(seconds=2),
            )
        else:
            store.supersede_decision(
                decision["decision_id"],
                reason_code="terminal",
                now=NOW + timedelta(seconds=2),
            )
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT lifecycle FROM executions WHERE execution_id=?",
            (execution["execution_id"],),
        ).fetchone()[0] == "terminal_ambiguous"
        assert connection.execute(
            "SELECT state FROM decisions WHERE decision_id=?",
            (decision["decision_id"],),
        ).fetchone()[0] == "pending"


def test_old_resolution_cannot_authorize_newer_pending_decision(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix="decision-generation")
    old = _resolved_decision(store, execution, suffix="old")
    newer = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest("request-newer"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(ContractConflictError, match="newer pending"):
        _record_evidence(
            store,
            execution,
            decision_id=old["decision_id"],
            suffix="old-resolution",
        )
    assert store.get_decision(newer["decision_id"])["state"] == "pending"


def test_evidence_blocks_new_decisions_but_preserves_idempotent_resolved_retry(
    tmp_path,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix="evidence-decision-gate")
    resolved = _resolved_decision(store, execution, suffix="evidence-decision-gate")
    evidence = _record_evidence(
        store,
        execution,
        decision_id=resolved["decision_id"],
        suffix="evidence-decision-gate",
    )
    duplicate = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest("request-evidence-decision-gate"),
        candidate_digest=_digest("candidate-evidence-decision-gate"),
        policy_digest=_digest("policy-evidence-decision-gate"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW + timedelta(seconds=3),
    )
    assert duplicate["decision_id"] == resolved["decision_id"]
    assert duplicate["state"] == "resolved"
    assert store.record_effect_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome="succeeded",
        subject_digest=_digest("subject-evidence-decision-gate"),
        evidence_digest=_digest("evidence-evidence-decision-gate"),
        result_digest=_digest("result-evidence-decision-gate"),
        decision_id=resolved["decision_id"],
        now=NOW + timedelta(seconds=4),
    )["evidence_id"] == evidence["evidence_id"]

    with pytest.raises(ContractConflictError, match="no new decision"):
        store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest("request-after-evidence"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=6),
            now=NOW + timedelta(seconds=3),
        )
    with pytest.raises(ContractConflictError, match="conflicting bindings"):
        store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest("request-evidence-decision-gate"),
            candidate_digest=_digest("candidate-conflict"),
            policy_digest=_digest("policy-evidence-decision-gate"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=3),
        )


@pytest.mark.parametrize(
    ("case", "expected_state"),
    [
        ("pending", "pending"),
        ("expired", "expired"),
        ("resolved", "resolved"),
        ("superseded", "superseded"),
        ("evidence", "resolved"),
        ("receipt", "resolved"),
    ],
)
def test_exact_decision_retry_remains_idempotent_after_expiry_and_effects(
    tmp_path,
    case,
    expected_state,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=f"retry-{case}")
    expires_at = NOW + timedelta(seconds=1)
    request_digest = _digest(f"retry-{case}")
    create = {
        "execution_id": execution["execution_id"],
        "effect_id": execution["effect_id"],
        "proposal_ref": execution["proposal_ref"],
        "request_digest": request_digest,
        "candidate_digest": _digest(f"candidate-retry-{case}"),
        "policy_digest": _digest(f"policy-retry-{case}"),
        "allowed_choices": ["once", "deny"],
        "expires_at": expires_at,
    }
    decision = store.create_decision(**create, now=NOW)

    if case == "expired":
        assert store.expire_decisions(now=NOW + timedelta(seconds=2)) == [
            decision["decision_id"]
        ]
    elif case in {"resolved", "evidence", "receipt"}:
        decision = store.resolve_decision(
            decision["decision_id"],
            choice="once",
            resolution_evidence_digest=_digest(f"resolution-retry-{case}"),
            now=NOW + timedelta(microseconds=500_000),
        )
        if case in {"evidence", "receipt"}:
            _record_evidence(
                store,
                execution,
                decision_id=decision["decision_id"],
                suffix=f"retry-{case}",
            )
        if case == "receipt":
            store.transition_execution(
                execution["execution_id"],
                "terminal_succeeded",
                now=NOW + timedelta(seconds=3),
            )
    elif case == "superseded":
        decision = store.supersede_decision(
            decision["decision_id"],
            reason_code="retry-test",
            now=NOW + timedelta(microseconds=500_000),
        )

    exact = store.create_decision(**create, now=NOW + timedelta(days=1))
    assert exact["decision_id"] == decision["decision_id"]
    assert exact["state"] == expected_state

    conflicting = dict(create)
    conflicting["allowed_choices"] = ["once", "deny", "always"]
    with pytest.raises(ContractConflictError, match="conflicting bindings"):
        store.create_decision(**conflicting, now=NOW + timedelta(days=1))


@pytest.mark.parametrize("first_operation", ["decision", "evidence"])
def test_decision_and_evidence_race_is_serialized_by_write_transaction(
    tmp_path,
    first_operation,
):
    seed = _store(tmp_path, runtime="race-runtime")
    execution = _bound_execution(seed, suffix=f"race-{first_operation}")
    decision_store = _store(tmp_path, runtime="race-runtime")
    evidence_store = _store(tmp_path, runtime="race-runtime")
    first_locked = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()

    def hold_first(operation, _execution_id):
        if operation == first_operation:
            first_locked.set()
            assert release_first.wait(timeout=5)

    if first_operation == "decision":
        decision_store._after_write_lock_acquired = hold_first
    else:
        evidence_store._after_write_lock_acquired = hold_first

    def request_decision():
        if first_operation != "decision":
            second_attempted.set()
        return decision_store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest(f"race-request-{first_operation}"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    def record_evidence():
        if first_operation != "evidence":
            second_attempted.set()
        return _record_evidence(
            evidence_store,
            execution,
            suffix=f"race-{first_operation}",
        )

    first_call = request_decision if first_operation == "decision" else record_evidence
    second_call = record_evidence if first_operation == "decision" else request_decision
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_call)
        assert first_locked.wait(timeout=5)
        second_future = pool.submit(second_call)
        assert second_attempted.wait(timeout=5)
        release_first.set()
        first_result = first_future.result(timeout=10)
        with pytest.raises(ContractConflictError):
            second_future.result(timeout=10)

    if first_operation == "decision":
        assert first_result["state"] == "pending"
        assert seed.list_decisions(state="pending")["items"] == [first_result]
        assert seed.list_events(execution_id=execution["execution_id"])["page"][
            "completeness"
        ] == "complete"
    else:
        assert first_result["outcome"] == "succeeded"
        assert seed.list_decisions()["items"] == []
        assert seed.get_execution(execution["execution_id"])["receipt_state"] == (
            "pending_evidence"
        )


def test_receipt_also_blocks_new_decisions(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix="receipt-decision-gate")
    store.transition_execution(execution["execution_id"], "running", now=NOW)
    _record_evidence(store, execution, suffix="receipt-decision-gate")
    store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        now=NOW + timedelta(seconds=3),
    )
    with pytest.raises(ContractConflictError, match="no new decision"):
        store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest("receipt-new-decision"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW + timedelta(seconds=4),
        )


def test_collection_snapshot_excludes_concurrent_insert(monkeypatch, tmp_path):
    import hermes_state

    monkeypatch.setattr(
        hermes_state,
        "apply_wal_with_fallback",
        lambda connection, **_kwargs: connection.execute("PRAGMA journal_mode=WAL"),
    )
    store = _store(tmp_path)
    writer = _store(tmp_path)
    store.create_execution(lifecycle="queued", source_run_id="snapshot-1", now=NOW)
    store.create_execution(lifecycle="queued", source_run_id="snapshot-2", now=NOW)
    writer.initialize()
    inserted = []

    def append_after_pin(collection, _high_water):
        if collection == "executions" and not inserted:
            inserted.append(
                writer.create_execution(
                    lifecycle="queued",
                    source_run_id="snapshot-concurrent",
                    now=NOW,
                )
            )

    store._after_snapshot_pinned = append_after_pin
    first = store.list_executions(limit=1)
    assert first["page"]["snapshot_high_water"] == 2
    assert inserted
    store._after_snapshot_pinned = lambda *_args: None
    second = store.list_executions(
        after=first["page"]["cursor"],
        snapshot_high_water=first["page"]["snapshot_high_water"],
    )
    source_ids = [item["source_run_id"] for item in first["items"] + second["items"]]
    assert source_ids == ["snapshot-1", "snapshot-2"]
    assert "snapshot-concurrent" not in source_ids


def test_event_snapshot_excludes_append_after_high_water(monkeypatch, tmp_path):
    import hermes_state

    monkeypatch.setattr(
        hermes_state,
        "apply_wal_with_fallback",
        lambda connection, **_kwargs: connection.execute("PRAGMA journal_mode=WAL"),
    )
    store = _store(tmp_path)
    writer = _store(tmp_path)
    first_execution = store.create_execution(lifecycle="queued", now=NOW)
    second_execution = store.create_execution(lifecycle="queued", now=NOW)
    writer.initialize()
    appended = []

    def append_after_pin(collection, _high_water):
        if collection == "events" and not appended:
            appended.append(
                writer.transition_execution(
                    second_execution["execution_id"],
                    "running",
                    now=NOW + timedelta(seconds=1),
                )
            )

    store._after_snapshot_pinned = append_after_pin
    page = store.list_events(limit=200)
    assert page["page"]["snapshot_high_water"] == 2
    assert appended
    assert [event["execution_id"] for event in page["items"]] == [
        first_execution["execution_id"],
        second_execution["execution_id"],
    ]


@pytest.mark.parametrize("gap", ["start", "interior", "end"])
def test_global_event_sequence_gaps_fail_closed_before_complete(tmp_path, gap):
    store = _store(tmp_path)
    for index in range(4):
        store.create_execution(
            lifecycle="queued",
            source_run_id=f"gap-{gap}-{index}",
            now=NOW + timedelta(microseconds=index),
        )
    sequence = {"start": 1, "interior": 2, "end": 4}[gap]
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM execution_events WHERE sequence=?",
            (sequence,),
        )
        connection.commit()

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events()


def test_filtered_event_feed_still_validates_unfiltered_global_continuity(tmp_path):
    store = _store(tmp_path)
    first = store.create_execution(lifecycle="queued", now=NOW)
    store.create_execution(
        lifecycle="queued",
        now=NOW + timedelta(microseconds=1),
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DELETE FROM execution_events WHERE sequence=2")
        connection.commit()

    with pytest.raises(ContractDataError, match="event sequence gap"):
        store.list_events(execution_id=first["execution_id"])


def test_empty_and_fully_pruned_event_windows_are_legitimately_complete(tmp_path):
    empty = _store(tmp_path, profile="empty")
    assert empty.list_events()["page"] == {
        "cursor": 0,
        "high_water": 0,
        "snapshot_high_water": 0,
        "minimum_available": 1,
        "has_more": False,
        "completeness": "complete",
        "pruned_through": 0,
        "retention_seconds": 2592000,
    }

    store = _store(tmp_path, profile="pruned")
    old = NOW - timedelta(days=40)
    execution = store.create_execution(lifecycle="running", now=old)
    store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    assert store.prune_events(now=NOW) == 2
    page = store.list_events(after=2, snapshot_high_water=2)
    assert page["items"] == []
    assert page["page"]["completeness"] == "complete"
    assert page["page"]["pruned_through"] == 2
    assert page["page"]["snapshot_high_water"] == 2


def test_pruning_after_snapshot_pin_does_not_create_false_gap(
    monkeypatch,
    tmp_path,
):
    import hermes_state

    monkeypatch.setattr(
        hermes_state,
        "apply_wal_with_fallback",
        lambda connection, **_kwargs: connection.execute("PRAGMA journal_mode=WAL"),
    )
    reader = _store(tmp_path)
    writer = _store(tmp_path)
    old = NOW - timedelta(days=40)
    execution = reader.create_execution(lifecycle="running", now=old)
    reader.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    writer.initialize()
    pruned = []

    def prune_after_pin(collection, _high_water):
        if collection == "events" and not pruned:
            pruned.append(writer.prune_events(now=NOW))

    reader._after_snapshot_pinned = prune_after_pin
    page = reader.list_events(after=0)
    assert pruned == [2]
    assert len(page["items"]) == 2
    assert page["page"]["completeness"] == "complete"
    reader._after_snapshot_pinned = lambda *_args: None
    with pytest.raises(ContractCursorGoneError):
        reader.list_events(after=0)


def test_late_ambiguous_evidence_publishes_receipt_transactionally(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix="late-ambiguous")
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        recovery_ref="recovery:late-ambiguous",
        now=NOW + timedelta(seconds=1),
    )
    assert terminal["lifecycle"] == "terminal_ambiguous"
    assert terminal["receipt_state"] == "unproven"

    with pytest.raises(ContractConflictError, match="reconciliation path"):
        _record_evidence(store, execution, outcome="ambiguous", suffix="late")

    receipt = store.reconcile_ambiguous_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome="ambiguous",
        subject_digest=_digest("late-subject"),
        evidence_digest=_digest("late-evidence"),
        result_digest=_digest("late-result"),
        reconciliation_ref="reconcile:late-ambiguous",
        now=NOW + timedelta(seconds=2),
    )
    current = store.get_execution(execution["execution_id"])
    assert receipt["outcome"] == "ambiguous"
    assert current["lifecycle"] == "terminal_ambiguous"
    assert current["receipt_state"] == "published"
    assert current["receipt_id"] == receipt["receipt_id"]
    assert store.get_receipt(receipt["receipt_id"])["reconciliation_ref"] == (
        "reconcile:late-ambiguous"
    )
    assert receipt["recovery_ref"] == "recovery:late-ambiguous"
    identical = store.reconcile_ambiguous_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome="ambiguous",
        subject_digest=_digest("late-subject"),
        evidence_digest=_digest("late-evidence"),
        result_digest=_digest("late-result"),
        reconciliation_ref="reconcile:late-ambiguous",
        now=NOW + timedelta(seconds=3),
    )
    assert identical["receipt_id"] == receipt["receipt_id"]
    with pytest.raises(ContractConflictError, match="not eligible"):
        store.reconcile_ambiguous_evidence(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            outcome="ambiguous",
            subject_digest=_digest("late-subject"),
            evidence_digest=_digest("late-evidence"),
            result_digest=_digest("late-result"),
            recovery_ref="recovery:conflicting-retry",
            reconciliation_ref="reconcile:late-ambiguous",
            now=NOW + timedelta(seconds=4),
        )


@pytest.mark.parametrize("offset_seconds", [-86400, 0, 86400])
def test_normal_receipt_terminal_timestamp_must_equal_execution_terminal(
    tmp_path,
    offset_seconds,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=f"receipt-time-{offset_seconds}")
    _record_evidence(
        store,
        execution,
        suffix=f"receipt-time-{offset_seconds}",
        reconciliation_ref=(
            "reconcile:ordinary-terminal" if offset_seconds == 0 else None
        ),
    )
    terminal_time = NOW + timedelta(seconds=3)
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        now=terminal_time,
    )
    receipt_id = terminal["receipt_id"]
    if offset_seconds:
        changed = (terminal_time + timedelta(seconds=offset_seconds)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "UPDATE receipts SET terminal_at=? WHERE receipt_id=?",
                (changed, receipt_id),
            )
            connection.commit()
        with pytest.raises(
            ContractDataError,
            match="must equal execution terminal timestamp",
        ):
            store.get_receipt(receipt_id)
    else:
        assert store.get_receipt(receipt_id)["terminal_at"] == terminal["terminal_at"]


@pytest.mark.parametrize("offset_seconds", [-1, 0, 1])
def test_reconciled_receipt_terminal_timestamp_must_equal_evidence_recorded_at(
    tmp_path,
    offset_seconds,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=f"reconcile-time-{offset_seconds}")
    store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        recovery_ref=f"recovery:reconcile-time-{offset_seconds}",
        now=NOW + timedelta(seconds=1),
    )
    evidence_time = NOW + timedelta(seconds=2)
    receipt = store.reconcile_ambiguous_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome="ambiguous",
        subject_digest=_digest(f"reconcile-subject-{offset_seconds}"),
        evidence_digest=_digest(f"reconcile-evidence-{offset_seconds}"),
        result_digest=_digest(f"reconcile-result-{offset_seconds}"),
        reconciliation_ref=f"reconcile:timestamp-{offset_seconds}",
        now=evidence_time,
    )
    if offset_seconds:
        changed = (evidence_time + timedelta(seconds=offset_seconds)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "UPDATE receipts SET terminal_at=? WHERE receipt_id=?",
                (changed, receipt["receipt_id"]),
            )
            connection.commit()
        with pytest.raises(
            ContractDataError,
            match="must equal evidence recorded timestamp",
        ):
            store.get_receipt(receipt["receipt_id"])
    else:
        assert store.get_receipt(receipt["receipt_id"])["terminal_at"] == (
            evidence_time.isoformat(timespec="microseconds").replace("+00:00", "Z")
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("execution_id", "identifier"),
        ("execution_timestamp", "timestamp ordering"),
        ("execution_receipt_state", "receipt"),
        ("decision_digest", "request_digest"),
        ("decision_binding", "binding"),
        ("receipt_digest", "evidence_digest"),
    ],
)
def test_deep_persisted_corruption_fails_before_projection(tmp_path, case, message):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=case)
    target = "execution"
    target_id = execution["execution_id"]

    if case.startswith("decision_"):
        decision = store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest(f"request-{case}"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
        target = "decision"
        target_id = decision["decision_id"]
    elif case == "receipt_digest":
        evidence = _record_evidence(store, execution, suffix=case)
        assert evidence["outcome"] == "succeeded"
        terminal = store.transition_execution(
            execution["execution_id"],
            "terminal_succeeded",
            now=NOW + timedelta(seconds=3),
        )
        target = "receipt"
        target_id = terminal["receipt_id"]

    with sqlite3.connect(store.database_path) as connection:
        if case == "execution_id":
            connection.execute(
                "UPDATE executions SET execution_id='bad-id' WHERE execution_id=?",
                (execution["execution_id"],),
            )
        elif case == "execution_timestamp":
            connection.execute(
                "UPDATE executions SET updated_at='2026-08-14T00:00:00.000000Z' "
                "WHERE execution_id=?",
                (execution["execution_id"],),
            )
        elif case == "execution_receipt_state":
            connection.execute(
                "UPDATE executions SET receipt_state='published', "
                "receipt_id=? WHERE execution_id=?",
                (
                    f"rcp_{store.authority.profile_key}_{'f' * 32}",
                    execution["execution_id"],
                ),
            )
        elif case == "decision_digest":
            connection.execute(
                "UPDATE decisions SET request_digest='bad' WHERE decision_id=?",
                (target_id,),
            )
        elif case == "decision_binding":
            connection.execute(
                "UPDATE decisions SET effect_id='effect:other' WHERE decision_id=?",
                (target_id,),
            )
        elif case == "receipt_digest":
            connection.execute(
                "UPDATE receipts SET evidence_digest='bad' WHERE receipt_id=?",
                (target_id,),
            )
        connection.commit()

    with pytest.raises(ContractDataError, match=message):
        if target == "execution":
            store.list_executions()
        elif target == "decision":
            store.get_decision(target_id)
        else:
            store.get_receipt(target_id)
