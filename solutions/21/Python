D=1,1j,-1,-1j
s=1+1j
T=202300
e=enumerate
g={x+y*1jfor y,L in e(open(0))for x,c in e(L)if'$'<c}
C=s,-s,s-2,s-2j
r=lambda t=64:lambda S=0:len(eval("g&{p+d for p in "*t+'{65*(S+s)}'+"for d in D}"*t))
print(r()(),T*(T*r(132)()+sum(map(r(),C)))+~-T*(~-T*r(131)()+sum(map(r(195),C)))+sum(map(r(130),D)))