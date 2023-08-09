export function useUrlParams(url: string) {
  let arr = url.split('?');
  let params = arr[1].split('&');
  let obj: { [propname: string]: string } = {};
  for (let i = 0; i < params.length; i++) {
    let param = params[i].split('=');
    obj[param[0]] = param[1];
  }
  return obj;
}
